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
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from finance_agent.identity import DEFAULT_PORT, HOST, current_user
from finance_agent import categorise
from finance_agent.ledger import (
    AccountBalance,
    Gap,
    Ledger,
    MonthSummary,
    Transaction,
    UnknownTarget,
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


class TransactionOut(BaseModel):
    id: int
    posted_on: str
    description: str
    amount: Money
    account_id: int
    kind: str | None
    merchant_id: int | None
    merchant: str | None
    category: list[str] | None  # [top, sub]
    source: str | None


class TransactionsOut(BaseModel):
    items: list[TransactionOut]
    total: int  # matching rows before the limit


class CategoryOut(BaseModel):
    name: str
    subs: list[str]


class CorrectionIn(BaseModel):
    # exactly one target: this Transaction, or every Transaction of its Merchant
    transaction_id: int | None = None
    merchant_id: int | None = None
    category: list[str] = Field(min_length=2, max_length=2)


class CorrectionOut(BaseModel):
    id: int
    ledger_version: int


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


def _transaction(t: Transaction) -> TransactionOut:
    c = t.categorisation
    return TransactionOut(
        id=t.id,
        posted_on=t.posted_on.isoformat(),
        description=t.description,
        amount=_money(t.amount, "GBP"),  # shortcut: GBP only, as in summarise_month
        account_id=t.account_id,
        kind=c.kind if c else None,
        merchant_id=c.merchant_id if c else None,
        merchant=c.merchant if c else None,
        category=list(c.category) if c and c.category else None,
        source=c.source if c else None,
    )


def _matches(
    t: Transaction,
    account_id: int | None,
    kind: str | None,
    category: str | None,
    month: str | None,
    needle: str,
) -> bool:
    c = t.categorisation
    return (
        (account_id is None or t.account_id == account_id)
        and (month is None or t.posted_on.strftime("%Y-%m") == month)
        and (kind is None or (c is not None and c.kind == kind))
        and (
            category is None
            or (c is not None and c.category is not None and c.category[0] == category)
        )
        and (
            not needle
            or needle in t.description.lower()
            or needle in ((c.merchant if c else None) or "").lower()
        )
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

    @app.get("/api/accounts")
    def accounts(led: Led) -> list[AccountOut]:
        return [_account(a) for a in led.accounts()]

    @app.get("/api/categories")
    def categories() -> list[CategoryOut]:
        return [
            CategoryOut(name=n, subs=list(subs))
            for n, subs in categorise.TAXONOMY.items()
        ]

    @app.get("/api/transactions")
    def transactions(
        led: Led,
        account_id: int | None = None,
        kind: str | None = None,
        category: str | None = None,  # top-level name
        month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> TransactionsOut:
        needle = q.strip().lower() if q else ""
        # shortcut: filters in Python over every row; fine for one person's Ledger
        rows = [
            t
            for t in reversed(led.transactions())
            if _matches(t, account_id, kind, category, month, needle)
        ]
        return TransactionsOut(
            items=[_transaction(t) for t in rows[offset : offset + limit]],
            total=len(rows),
        )

    @app.post("/api/corrections", status_code=201)
    def correct(led: Led, body: CorrectionIn) -> CorrectionOut:
        if (body.transaction_id is None) == (body.merchant_id is None):
            raise HTTPException(422, "give exactly one of transaction_id, merchant_id")
        cat = (body.category[0], body.category[1])
        try:
            if body.transaction_id is not None:
                k = led.correct_transaction(body.transaction_id, cat)
            else:
                assert body.merchant_id is not None
                k = led.correct_merchant(body.merchant_id, cat)
        except UnknownTarget:
            raise HTTPException(404, "no such Transaction, Merchant or Category") from None
        return CorrectionOut(id=k.id, ledger_version=led.ledger_version())

    dist = web_dir or WEB_DIST
    if dist.is_dir():
        # registered last so /api/* wins
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def serve(port: int = DEFAULT_PORT) -> None:
    uvicorn.run(create_app(open_db()), host=HOST, port=port, log_level="warning")
