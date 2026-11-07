"""The FX endpoints (#156), for `current_user` only: enter a rate, and the
provenance behind the GBP totals (which rate each foreign figure used, and what
is left out as "rate needed"). Rates are Decimal strings both ways, never JSON
numbers, and are never fetched: no egress."""

from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from finance_agent.identity import current_user
from finance_agent.ledger import Ledger
from finance_agent.ledger import fx as ledger_fx

MonthQ = Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]


class Money(BaseModel):
    minor: int
    currency: str


class RateOut(BaseModel):
    currency: str
    rate: str  # GBP per one unit of `currency`, a Decimal string
    on: str  # the date the rate is from, ISO
    source: Literal["bank", "exchange", "user"]


class RateUsedOut(RateOut):
    count: int  # how many of the month's foreign Transactions used it


class RateNeededOut(BaseModel):
    currency: str
    count: int  # foreign Transactions left out of the totals


class FxMonthOut(BaseModel):
    month: str
    rates: list[RateUsedOut]
    rate_needed: list[RateNeededOut]


class AccountGbpOut(BaseModel):
    account_id: int
    currency: str
    balance: Money  # the Bank's, in the Account's own currency
    gbp: Money | None  # None: rate needed
    rate: RateOut | None


class BalancesOut(BaseModel):
    total: Money | None  # None: a balance is unknown or needs a rate
    accounts: list[AccountGbpOut]  # foreign-currency Accounts only


class RateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    # digits only: no exponent, sign, NaN or Infinity; the range is checked too
    rate: str = Field(pattern=r"^[0-9]{1,7}(\.[0-9]{1,12})?$")  # ASCII
    on: date


def _rate(r: ledger_fx.Rate) -> RateOut:
    return RateOut(
        currency=r.currency,
        rate=str(r.gbp_per_unit),
        on=r.on.isoformat(),
        source=r.source,  # pyright: ignore[reportArgumentType]
    )


def _ledger(request: Request) -> Iterator[Ledger]:
    with request.app.state.engine.begin() as conn:
        yield Ledger(conn, current_user())


Led = Annotated[Ledger, Depends(_ledger)]
router = APIRouter(prefix="/api/fx")


@router.get("/rates")
def rates(led: Led) -> list[RateOut]:
    """Rates from the user's own exchanges, then those entered."""
    return [_rate(r) for r in led.fx_rates()]


@router.put("/rates")
def set_rate(led: Led, body: RateIn) -> RateOut:
    try:
        return _rate(led.set_rate(body.currency, Decimal(body.rate), body.on))
    except ledger_fx.InvalidRate as e:
        raise HTTPException(422, str(e)) from None


@router.get("/month")
def month(
    led: Led, month: MonthQ, account_id: Annotated[int | None, Query()] = None
) -> FxMonthOut:
    if account_id is not None and account_id not in {a.id for a in led.accounts()}:
        raise HTTPException(404, "No such account.")
    try:
        m = led.fx_month(month, account_id)
    except ValueError:
        raise HTTPException(422, "month must be a real YYYY-MM") from None
    used = Counter(c.rate for c in m.conversions)
    needed = Counter(t.currency for t in m.rate_needed)
    return FxMonthOut(
        month=month,
        rates=[
            RateUsedOut(**_rate(r).model_dump(), count=n)
            for r, n in sorted(used.items(), key=lambda kv: (kv[0].currency, kv[0].on))
        ],
        rate_needed=[
            RateNeededOut(currency=c, count=n) for c, n in sorted(needed.items())
        ],
    )


@router.get("/balances")
def balances(led: Led) -> BalancesOut:
    b = led.balances_in_gbp()
    own = {a.id: a for a in led.accounts()}
    out: list[AccountGbpOut] = []
    for x in b.accounts:
        a = own[x.account_id]
        c = x.conversion
        out.append(
            AccountGbpOut(
                account_id=a.id,
                currency=a.currency,
                balance=Money(
                    minor=int((a.bank_balance or Decimal(0)).scaleb(2)),
                    currency=a.currency,
                ),
                gbp=None if c is None else Money(minor=c.gbp_minor, currency="GBP"),
                rate=None if c is None else _rate(c.rate),
            )
        )
    total = (
        None if b.total is None else Money(minor=int(b.total.scaleb(2)), currency="GBP")
    )
    return BalancesOut(total=total, accounts=out)
