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

from finance_agent.ledger import AccountBalance, Gap, Ledger, MonthSummary
from finance_agent.storage import open_db

HOST = "127.0.0.1"  # never 0.0.0.0: nothing on the network may read the Ledger
DEFAULT_PORT = 8765
# shortcut: located relative to the source tree, so only a checkout serves the UI
WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def current_user() -> int:
    """The single local user until the demo milestone (ADR 0003)."""
    return 1


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

    dist = web_dir or WEB_DIST
    if dist.is_dir():
        # registered last so /api/* wins
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


def serve(port: int = DEFAULT_PORT) -> None:
    uvicorn.run(create_app(open_db()), host=HOST, port=port, log_level="warning")
