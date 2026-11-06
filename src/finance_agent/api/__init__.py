"""The local API: a thin adapter over the Ledger interface, 127.0.0.1 only.

Money crosses the wire as integer minor units plus a currency; the frontend
formats it in one place."""

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Engine

from finance_agent.identity import DEFAULT_PORT, HOST, current_user
from finance_agent.ledger import (
    AccountBalance,
    Breakdown,
    CategoryDetail,
    Gap,
    Ledger,
    MonthSummary,
    Spend,
    Transaction,
)
from finance_agent.storage import open_db

# shortcut: located relative to the source tree, so only a checkout serves the UI
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class Money(BaseModel):
    minor: int
    currency: str


class GapOut(BaseModel):
    from_date: str
    to_date: str
    amount: Money


class SummaryOut(BaseModel):
    month: str
    currency: str
    money_in: Money
    money_out: Money
    net: Money
    gaps: list[GapOut]


class AccountOut(BaseModel):
    id: int
    masked_number: str
    name: str
    kind: str
    currency: str
    bank_balance: Money
    ledger_balance: Money
    gaps: list[GapOut]


class CategorySpendOut(BaseModel):
    name: str
    amount: Money


class SpendingOut(BaseModel):
    month: str
    currency: str
    total: Money  # spending after refunds
    categories: list[CategorySpendOut]  # Uncategorised is gross
    unmatched_refunds: Money  # <= 0: Refunds with no Category
    refunds: Money  # all Refunds received
    fees: Money
    money_out: Money  # == total + refunds + fees


class TrendPointOut(BaseModel):
    month: str
    amount: Money


class CategoryTxnOut(BaseModel):
    id: int
    posted_on: str
    description: str
    amount: Money  # signed as the Bank has it: spending negative
    merchant: str | None
    subcategory: str | None


class CategoryOut(BaseModel):
    name: str
    month: str
    currency: str
    total: Money  # spending after refunds, as on the donut
    subcategories: list[CategorySpendOut]
    merchants: list[CategorySpendOut]  # top 5 across Subcategories
    trend: list[TrendPointOut]  # trailing 6 months, oldest first
    transactions: list[CategoryTxnOut]  # newest first


class HealthOut(BaseModel):
    app: str


def _money(amount: Decimal, currency: str) -> Money:
    return Money(minor=int(amount.scaleb(2)), currency=currency)


def _gap(g: Gap, currency: str) -> GapOut:
    return GapOut(
        from_date=g.from_date.isoformat(),
        to_date=g.to_date.isoformat(),
        amount=_money(g.amount, currency),
    )


def _summary(s: MonthSummary) -> SummaryOut:
    c = s.currency
    return SummaryOut(
        month=s.month,
        currency=c,
        money_in=_money(s.money_in, c),
        money_out=_money(s.money_out, c),
        net=_money(s.net, c),
        gaps=[_gap(g, c) for g in s.gaps],
    )


def _account(a: AccountBalance) -> AccountOut:
    return AccountOut(
        id=a.id,
        masked_number=a.masked_number,
        name=a.name,
        kind=a.kind,
        currency=a.currency,
        bank_balance=_money(a.bank_balance, a.currency),
        ledger_balance=_money(a.ledger_balance, a.currency),
        gaps=[_gap(g, a.currency) for g in a.gaps],
    )


def _spending(month: str, b: Breakdown, money_out: Decimal) -> SpendingOut:
    c = "GBP"
    return SpendingOut(
        month=month,
        currency=c,
        total=_money(b.spending_after_refunds, c),
        categories=[
            CategorySpendOut(name=s.name, amount=_money(s.amount, c))
            for s in b.categories
        ],
        unmatched_refunds=_money(b.unmatched_refunds, c),
        refunds=_money(b.refunds, c),
        fees=_money(b.fees, c),
        money_out=_money(money_out, c),
    )


def _txn(t: Transaction) -> CategoryTxnOut:
    c = t.categorisation
    return CategoryTxnOut(
        id=t.id,
        posted_on=t.posted_on.isoformat(),
        description=t.description,
        amount=_money(t.amount, "GBP"),
        merchant=c.merchant if c else None,
        subcategory=c.category[1] if c and c.category else None,
    )


def _category(month: str, d: CategoryDetail) -> CategoryOut:
    c = "GBP"

    def spends(xs: tuple[Spend, ...]) -> list[CategorySpendOut]:
        return [CategorySpendOut(name=x.name, amount=_money(x.amount, c)) for x in xs]

    return CategoryOut(
        name=d.name,
        month=month,
        currency=c,
        total=_money(d.total, c),
        subcategories=spends(d.subcategories),
        merchants=spends(d.merchants),
        trend=[TrendPointOut(month=m, amount=_money(a, c)) for m, a in d.trend],
        transactions=[_txn(t) for t in d.transactions],
    )


def _ledger(request: Request) -> Iterator[Ledger]:
    engine: Engine = request.app.state.engine
    with engine.begin() as conn:
        yield Ledger(conn, current_user())


Led = Annotated[Ledger, Depends(_ledger)]


def create_app(engine: Engine, web_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Finance Agent", docs_url=None, redoc_url=None)

    app.state.engine = engine

    @app.get("/api/health")
    def health() -> HealthOut:  # the launcher uses this to tell our server apart
        return HealthOut(app="finance-agent")

    @app.get("/api/summary")
    def summary(
        led: Led, month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None
    ) -> SummaryOut:
        month = month or led.latest_month()
        if month is None:
            raise HTTPException(404, "No transactions yet. Run `fa import` first.")
        try:
            return _summary(led.summarise_month(month))
        except ValueError:
            raise HTTPException(422, "month must be a real YYYY-MM") from None

    @app.get("/api/spending")
    def spending(
        led: Led, month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None
    ) -> SpendingOut:
        month = month or led.latest_month()
        if month is None:
            raise HTTPException(404, "No transactions yet. Run `fa import` first.")
        try:
            return _spending(
                month,
                led.category_breakdown(month),
                led.summarise_month(month).money_out,
            )
        except ValueError:
            raise HTTPException(422, "month must be a real YYYY-MM") from None

    @app.get("/api/categories/{name}")
    def category(
        led: Led,
        name: str,
        month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
    ) -> CategoryOut:
        month = month or led.latest_month()
        if month is None:
            raise HTTPException(404, "No transactions yet. Run `fa import` first.")
        try:
            detail = led.category_detail(name, month)
        except ValueError:
            raise HTTPException(422, "month must be a real YYYY-MM") from None
        if detail is None:
            raise HTTPException(404, "No spending in that Category this month.")
        return _category(month, detail)

    @app.get("/api/months")
    def months(led: Led) -> list[str]:
        return led.months()

    @app.get("/api/accounts")
    def accounts(led: Led) -> list[AccountOut]:
        return [_account(a) for a in led.accounts()]

    dist = web_dir or WEB_DIST
    if dist.is_dir():
        # registered last so /api/* wins
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def serve(port: int = DEFAULT_PORT) -> None:
    uvicorn.run(create_app(open_db()), host=HOST, port=port, log_level="warning")
