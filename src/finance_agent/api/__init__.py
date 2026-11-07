"""The local API: a thin adapter over the Ledger interface, 127.0.0.1 only.

Money crosses the wire as integer minor units plus a currency; the frontend
formats it in one place."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Engine

from finance_agent import categorise
from finance_agent.api import card_art, subscriptions
from finance_agent.identity import DEFAULT_PORT, HOST, current_user
from finance_agent.ledger import (
    CURRENCY,
    AccountBalance,
    Breakdown,
    CategoryDetail,
    Gap,
    KindHasNoCategory,
    Ledger,
    MonthSummary,
    Spend,
    Transaction,
    UnknownTarget,
)
from finance_agent.storage import backup, default_db_path, open_db

# shortcut: located relative to the source tree, so only a checkout serves the UI
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


class Money(BaseModel):
    minor: int
    currency: str


class GapOut(BaseModel):
    from_date: str
    to_date: str
    amount: Money


class AccountMonthOut(BaseModel):
    account_id: int
    money_in: Money
    money_out: Money
    transfers_in: Money
    transfers_out: Money


class SummaryOut(BaseModel):
    month: str
    currency: str
    money_in: Money  # Transfers between the user's own Accounts left out
    money_out: Money
    net: Money
    gaps: list[GapOut]
    transfers_in: Money
    transfers_out: Money
    accounts: list[AccountMonthOut]  # combined view only: each Account's part


class AccountOut(BaseModel):
    id: int
    bank: str
    masked_number: str
    name: str
    kind: str
    currency: str
    bank_balance: Money
    ledger_balance: Money
    gaps: list[GapOut]
    art: str | None  # key for /api/card-art/{art}; None means the generic face
    last_import: str | None  # ISO time of the newest statement with its rows
    source: str  # how that statement came in: "csv"


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


class CategorySpendOut(BaseModel):
    name: str
    amount: Money


class MerchantSpendOut(BaseModel):
    merchant_id: int | None  # None for "Unknown": no Merchant
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
    merchants: list[CategorySpendOut]  # top 5 across Categories


class MonthTotalsOut(BaseModel):
    month: str
    currency: str
    money_in: Money
    money_out: Money


class TrendPointOut(BaseModel):
    month: str
    amount: Money


class CategoryTxnOut(BaseModel):
    id: int
    posted_on: str
    description: str
    amount: Money  # signed as the Bank has it: spending negative
    merchant_id: int | None
    merchant: str | None
    subcategory: str | None


class CategoryDetailOut(BaseModel):
    name: str
    month: str
    currency: str
    total: Money  # spending after refunds, as on the donut
    subcategories: list[CategorySpendOut]
    merchants: list[MerchantSpendOut]  # top 5 across Subcategories
    trend: list[TrendPointOut]  # trailing 6 months, oldest first
    transactions: list[CategoryTxnOut]  # newest first


class SyncStatusOut(BaseModel):
    """Freshness and consent only: a failure is an error class and a count."""

    last_sync_at: str | None
    last_failure_at: str | None
    failure_class: str | None
    failures_in_a_row: int
    consent_expires_on: str | None
    consent_days_left: int | None
    consent_warning: bool
    last_backup_at: str | None  # the last good copy on the backup drive (#95)
    backup_warning: bool  # a drive is registered but no copy in 7 days


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


def _summary(
    s: MonthSummary, parts: dict[int, MonthSummary] | None = None
) -> SummaryOut:
    c = s.currency
    return SummaryOut(
        month=s.month,
        currency=c,
        money_in=_money(s.money_in, c),
        money_out=_money(s.money_out, c),
        net=_money(s.net, c),
        gaps=[_gap(g, c) for g in s.gaps],
        transfers_in=_money(s.transfers_in, c),
        transfers_out=_money(s.transfers_out, c),
        accounts=[
            AccountMonthOut(
                account_id=i,
                money_in=_money(p.money_in, c),
                money_out=_money(p.money_out, c),
                transfers_in=_money(p.transfers_in, c),
                transfers_out=_money(p.transfers_out, c),
            )
            for i, p in (parts or {}).items()
        ],
    )


def _account(a: AccountBalance) -> AccountOut:
    return AccountOut(
        id=a.id,
        bank=a.bank,
        masked_number=a.masked_number,
        name=a.name,
        kind=a.kind,
        currency=a.currency,
        bank_balance=_money(a.bank_balance, a.currency),
        ledger_balance=_money(a.ledger_balance, a.currency),
        gaps=[_gap(g, a.currency) for g in a.gaps],
        art=card_art.art_key(a.bank),
        last_import=a.last_import.isoformat() if a.last_import else None,
        source=a.source,
    )


def _transaction(t: Transaction) -> TransactionOut:
    c = t.categorisation
    return TransactionOut(
        id=t.id,
        posted_on=t.posted_on.isoformat(),
        description=t.description,
        amount=_money(t.amount, CURRENCY),
        account_id=t.account_id,
        kind=c.kind if c else None,
        merchant_id=c.merchant_id if c else None,
        merchant=c.merchant if c else None,
        category=list(c.category) if c and c.category else None,
        source=c.source if c else None,
    )


def _spending(month: str, b: Breakdown, money_out: Decimal) -> SpendingOut:
    c = CURRENCY
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
        merchants=[
            CategorySpendOut(name=s.name, amount=_money(s.amount, c))
            for s in b.merchants
        ],
    )


def _txn(t: Transaction) -> CategoryTxnOut:
    c = t.categorisation
    return CategoryTxnOut(
        id=t.id,
        posted_on=t.posted_on.isoformat(),
        description=t.description,
        amount=_money(t.amount, CURRENCY),
        merchant_id=c.merchant_id if c else None,
        merchant=c.merchant if c else None,
        subcategory=c.category[1] if c and c.category else None,
    )


def _category(month: str, d: CategoryDetail) -> CategoryDetailOut:
    c = CURRENCY

    def spends(xs: tuple[Spend, ...]) -> list[CategorySpendOut]:
        return [CategorySpendOut(name=x.name, amount=_money(x.amount, c)) for x in xs]

    return CategoryDetailOut(
        name=d.name,
        month=month,
        currency=c,
        total=_money(d.total, c),
        subcategories=spends(d.subcategories),
        merchants=[
            MerchantSpendOut(
                merchant_id=x.merchant_id, name=x.name, amount=_money(x.amount, c)
            )
            for x in d.merchants
        ],
        trend=[TrendPointOut(month=m, amount=_money(a, c)) for m, a in d.trend],
        transactions=[_txn(t) for t in d.transactions],
    )


def _conn(request: Request) -> Iterator[Connection]:
    engine: Engine = request.app.state.engine
    with engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]  # one per request, shared with Led


def _ledger(conn: Conn) -> Ledger:
    return Ledger(conn, current_user())


Led = Annotated[Ledger, Depends(_ledger)]
AccountId = Annotated[int | None, Query()]


def _known(led: Ledger, account_id: int | None) -> None:
    if account_id is not None and account_id not in {a.id for a in led.accounts()}:
        raise HTTPException(404, "No such account.")


def create_app(engine: Engine, web_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Finance Agent", docs_url=None, redoc_url=None)

    app.state.engine = engine

    @app.get("/api/health")
    def health() -> HealthOut:  # the launcher uses this to tell our server apart
        return HealthOut(app="finance-agent")

    @app.get("/api/summary")
    def summary(
        led: Led,
        month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
        account_id: AccountId = None,
    ) -> SummaryOut:
        _known(led, account_id)
        month = month or led.latest_month()
        if month is None:
            raise HTTPException(404, "No transactions yet. Run `fa import` first.")
        try:
            whole = led.summarise_month(month, account_id)
            parts = (
                {a.id: led.summarise_month(month, a.id) for a in led.accounts()}
                if account_id is None
                else None
            )
            return _summary(whole, parts)
        except ValueError:
            raise HTTPException(422, "month must be a real YYYY-MM") from None

    @app.get("/api/spending")
    def spending(
        led: Led,
        month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
        account_id: AccountId = None,
    ) -> SpendingOut:
        _known(led, account_id)
        month = month or led.latest_month()
        if month is None:
            raise HTTPException(404, "No transactions yet. Run `fa import` first.")
        try:
            return _spending(
                month,
                led.category_breakdown(month, account_id),
                led.summarise_month(month, account_id).money_out,
            )
        except ValueError:
            raise HTTPException(422, "month must be a real YYYY-MM") from None

    @app.get("/api/categories/{name}")
    def category(
        led: Led,
        name: str,
        month: Annotated[str | None, Query(pattern=r"^\d{4}-\d{2}$")] = None,
        account_id: AccountId = None,
    ) -> CategoryDetailOut:
        _known(led, account_id)
        month = month or led.latest_month()
        if month is None:
            raise HTTPException(404, "No transactions yet. Run `fa import` first.")
        try:
            detail = led.category_detail(name, month, account_id=account_id)
        except ValueError:
            raise HTTPException(422, "month must be a real YYYY-MM") from None
        if detail is None:
            raise HTTPException(404, "No spending in that Category this month.")
        return _category(month, detail)

    @app.get("/api/months")
    def months(led: Led) -> list[str]:
        return led.months()

    @app.get("/api/month-series")
    def month_series(
        led: Led,
        limit: Annotated[int, Query(ge=1, le=60)] = 12,
        account_id: AccountId = None,
    ) -> list[MonthTotalsOut]:
        """Newest `limit` months, oldest first; each is `summarise_month`."""
        _known(led, account_id)
        out: list[MonthTotalsOut] = []
        for m in reversed(led.months()[:limit]):
            s = led.summarise_month(m, account_id)
            out.append(
                MonthTotalsOut(
                    month=m,
                    currency=s.currency,
                    money_in=_money(s.money_in, s.currency),
                    money_out=_money(s.money_out, s.currency),
                )
            )
        return out

    @app.get("/api/accounts")
    def accounts(led: Led) -> list[AccountOut]:
        return [_account(a) for a in led.accounts()]

    @app.get("/api/sync-status")
    def sync_status(led: Led) -> SyncStatusOut:
        st = led.sync_status()
        bk = backup.status(default_db_path().parent, datetime.now(UTC))

        def iso(v: object) -> str | None:
            return v.isoformat() if isinstance(v, (date, datetime)) else None

        return SyncStatusOut(
            last_sync_at=iso(st.last_sync_at),
            last_failure_at=iso(st.last_failure_at),
            failure_class=st.failure_class,
            failures_in_a_row=st.failures_in_a_row,
            consent_expires_on=iso(st.consent_expires_on),
            consent_days_left=st.consent_days_left,
            consent_warning=st.consent_warning,
            last_backup_at=iso(bk.last_backup_at),
            backup_warning=bk.warning,
        )

    @app.get(
        "/api/card-art/{key}",
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}, 404: {}, 413: {}, 415: {}},
    )
    def card_image(key: str) -> Response:
        """The user's local card image, or 404 so the app draws the generic face."""
        try:
            data, media = card_art.read(key)
        except card_art.Refused as e:
            raise HTTPException(e.status, "No card art.") from None
        return Response(
            data,
            media_type=media,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

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
        page = led.search_transactions(
            account_id, kind, category, month, q, limit, offset
        )
        return TransactionsOut(
            items=[_transaction(t) for t in page.items], total=page.total
        )

    @app.post("/api/corrections", status_code=201)
    def correct(led: Led, conn: Conn, body: CorrectionIn) -> CorrectionOut:
        if (body.transaction_id is None) == (body.merchant_id is None):
            raise HTTPException(422, "give exactly one of transaction_id, merchant_id")
        cat = (body.category[0], body.category[1])
        # /api/categories offers the current version even before the next import
        categorise.seed_taxonomy(conn, current_user())
        try:
            if body.transaction_id is not None:
                k = led.correct_transaction(body.transaction_id, cat)
            elif body.merchant_id is not None:
                k = led.correct_merchant(body.merchant_id, cat)
            else:  # unreachable: checked above
                raise HTTPException(422, "no target")
        except UnknownTarget:
            raise HTTPException(
                404, "no such Transaction, Merchant or Category"
            ) from None
        except KindHasNoCategory as e:
            raise HTTPException(422, f"{e}: only Spending and Refunds do") from None
        return CorrectionOut(id=k.id, ledger_version=led.ledger_version())

    @app.delete("/api/corrections/{correction_id}")
    def revert(led: Led, correction_id: int) -> CorrectionOut:
        """Undo a Correction (it stays in the history, marked reverted)."""
        try:
            led.revert_correction(correction_id)
        except UnknownTarget:
            raise HTTPException(404, "no such active Correction") from None
        return CorrectionOut(id=correction_id, ledger_version=led.ledger_version())

    app.include_router(subscriptions.router)

    dist = web_dir or WEB_DIST
    if dist.is_dir():
        # registered last so /api/* wins
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def serve(port: int = DEFAULT_PORT) -> None:
    uvicorn.run(
        create_app(open_db(create=False)), host=HOST, port=port, log_level="warning"
    )
