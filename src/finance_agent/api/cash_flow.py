"""What-if purchases (#151): will this purchase take the projected balance
below zero, or below the user's buffer, before the next payday? For
`current_user` only; figures are integer pence from `engine.shortfall`."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from finance_agent.api.subscription_costs import Pence
from finance_agent.api.subscriptions import Led
from finance_agent.engine.baselines import Insufficient
from finance_agent.engine.forecast import cash_flow
from finance_agent.engine.shortfall import Risk, outlook, pace

router = APIRouter(prefix="/api/cash-flow")

HORIZON = 3  # months: the payday after a purchase late in the next cycle
MAX_MINOR = 10**13  # £100bn: beyond any balance, still exact in every client
Minor = Annotated[int, Query(ge=0, le=MAX_MINOR)]


class SimulationOut(BaseModel):
    status: Literal["ok", "insufficient"]
    payday: str | None  # ISO date of the first payday after the purchase
    minimum: Pence | None  # the lowest projected balance before it
    on: str | None  # ISO date it is first reached
    risk: Risk | None
    floor: Pence | None  # the bottom of the balance's band
    have: int | None  # insufficient: pay cycles (or paydays) so far
    need: int | None


@router.get("/simulate-purchase")
def simulate_purchase(
    led: Led,
    amount_minor: Annotated[int, Query(ge=1, le=MAX_MINOR)],
    on: date | None = None,
    buffer_minor: Minor = 0,
) -> SimulationOut:
    today = date.today()
    when = on or today
    if when < today:
        raise HTTPException(422, "A purchase cannot be in the past.")
    balances = [
        a.bank_balance if a.bank_balance is not None else a.ledger_balance
        for a in led.accounts()
        if a.currency == "GBP"
    ]
    known = [b for b in balances if b is not None]
    if not known:
        raise HTTPException(404, "No GBP balance yet. Run `fa import` first.")
    flow = cash_flow(led, today, HORIZON)
    o = outlook(
        int(sum(known) * 100),
        today,
        flow,
        pace(led.transactions(), flow, today),
        amount_minor,
        when,
        buffer_minor,
    )
    if isinstance(o, Insufficient):
        return SimulationOut(
            status="insufficient",
            payday=None,
            minimum=None,
            on=None,
            risk=None,
            floor=None,
            have=o.have,
            need=o.need,
        )
    return SimulationOut(
        status="ok",
        payday=o.payday.isoformat(),
        minimum=Pence(minor=o.minimum),
        on=o.on.isoformat(),
        risk=o.risk,
        floor=Pence(minor=o.floor),
        have=None,
        need=None,
    )
