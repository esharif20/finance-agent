"""Plan's tools (#155, spec #134 item 10): the cash-flow forecast and a what-if
purchase, from `engine.forecast` and `engine.shortfall` exactly as
`/api/cash-flow/simulate-purchase` calls them. Figures are integer pence from
code. Read-only: a simulated purchase is never made, proposed or recorded, and
there is no payee or account field to put one in (no payments, ever)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finance_agent.assistant.tools.read import Money, NoData, Source
from finance_agent.engine.baselines import Insufficient
from finance_agent.engine.forecast import cash_flow
from finance_agent.engine.shortfall import Risk, outlook, pace
from finance_agent.ledger import CURRENCY, Ledger

_STRICT = ConfigDict(extra="forbid", frozen=True)
HORIZON = 3  # months, as the what-if endpoint
MAX_MINOR = 10**13  # as the what-if endpoint


def _gbp(minor: int) -> Money:
    return Money(minor=minor, currency=CURRENCY)


# --- forecast_cashflow -------------------------------------------------------


class ForecastCashflowIn(BaseModel):
    model_config = _STRICT
    months: int = Field(default=HORIZON, ge=1, le=6)


class ForecastMonth(BaseModel):
    model_config = _STRICT
    evidence_id: str
    month: str
    money_out: Money  # the point forecast: recurring plus forecast Categories
    low: Money  # the 80% band
    high: Money
    recurring: Money  # from schedules
    income: Money  # recurring credits only


class ForecastCashflowOut(BaseModel):
    model_config = _STRICT
    months: list[ForecastMonth]
    left_out: int  # Categories with too little history, not in the totals


def forecast_cashflow(src: Source, inp: ForecastCashflowIn) -> ForecastCashflowOut:
    flow = cash_flow(Ledger(src.conn, src.user_id), src.today, inp.months)
    return ForecastCashflowOut(
        months=[
            ForecastMonth(
                evidence_id=f"forecast:{m.month}",
                month=m.month,
                money_out=_gbp(m.total.point),
                low=_gbp(m.total.lower),
                high=_gbp(m.total.upper),
                recurring=_gbp(m.recurring),
                income=_gbp(m.income),
            )
            for m in flow.months
        ],
        left_out=sum(c.method == "insufficient" for c in flow.categories.values()),
    )


# --- simulate_purchase -------------------------------------------------------


class SimulatePurchaseIn(BaseModel):
    model_config = _STRICT
    amount_minor: int = Field(ge=1, le=MAX_MINOR)
    on: date | None = None  # default today; never in the past
    buffer_minor: int = Field(default=0, ge=0, le=MAX_MINOR)


class SimulatePurchaseOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    status: Literal["ok", "insufficient"]
    purchase: Money  # the user's own figures, echoed
    buffer: Money
    payday: date | None  # the first payday after the purchase
    minimum: Money | None  # the lowest projected balance before it
    lowest_on: date | None  # the day it is first reached
    risk: Risk | None
    floor: Money | None  # the bottom of the balance's band
    have: int | None  # insufficient: pay cycles so far, of `need`
    need: int | None


def simulate_purchase(src: Source, inp: SimulatePurchaseIn) -> SimulatePurchaseOut:
    when = inp.on or src.today
    if when < src.today:
        raise NoData("a purchase cannot be in the past")
    led = Ledger(src.conn, src.user_id)
    known = [
        b
        for a in led.accounts()
        if a.currency == CURRENCY
        for b in (a.bank_balance if a.bank_balance is not None else a.ledger_balance,)
        if b is not None
    ]
    if not known:
        raise NoData("no GBP balance yet")
    flow = cash_flow(led, src.today, HORIZON)
    o = outlook(
        int(sum(known) * 100),
        src.today,
        flow,
        pace(led.transactions(), flow, src.today),
        inp.amount_minor,
        when,
        inp.buffer_minor,
    )
    short = isinstance(o, Insufficient)
    return SimulatePurchaseOut(
        evidence_id=f"whatif:{inp.amount_minor}:{when}:{inp.buffer_minor}",
        status="insufficient" if short else "ok",
        purchase=_gbp(inp.amount_minor),
        buffer=_gbp(inp.buffer_minor),
        payday=None if short else o.payday,
        minimum=None if short else _gbp(o.minimum),
        lowest_on=None if short else o.on,
        risk=None if short else o.risk,
        floor=None if short else _gbp(o.floor),
        have=o.have if short else None,
        need=o.need if short else None,
    )
