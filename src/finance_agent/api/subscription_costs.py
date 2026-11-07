"""Subscription status, monthly cost and totals (#105), and the user's
"I cancelled it", for `current_user` only. Monthly figures are exact until
here, where each one is rounded half-even to the penny once."""

from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from finance_agent.api.subscriptions import (
    Led,
    MerchantId,
    SubscriptionOut,
    apply_change,
)
from finance_agent.categorise.subscriptions import costs, display

router = APIRouter(prefix="/api/subscriptions")


class Pence(BaseModel):  # the shape of the main API's Money
    minor: int
    currency: str = "GBP"


class CostOut(BaseModel):
    merchant_id: int
    merchant: str
    kind: str
    group: str
    status: str  # active, overdue, lapsed, cancelled or suggested
    interval: str | None
    last_charge_on: str | None  # ISO date
    monthly: Pence | None  # None with a cost_note
    cost_note: str | None  # rate_needed, interval_needed or no_charge
    counted: bool  # in the totals: active or overdue, with a cost


class KindTotal(BaseModel):
    kind: str
    monthly: Pence


class GroupTotal(KindTotal):
    group: str


class CostsOut(BaseModel):
    items: list[CostOut]
    totals: list[KindTotal]  # per kind: the exact sum of its counted items
    groups: list[GroupTotal]
    rate_needed: int  # items left out of totals until an FX rate exists


@router.get("/costs")
def subscription_costs(led: Led) -> CostsOut:
    s = costs(led, date.today())
    return CostsOut(
        items=[
            CostOut(
                merchant_id=c.entry.merchant_id,
                merchant=c.entry.merchant,
                kind=c.entry.kind or "",
                group=c.entry.group or "",
                status=c.status,
                interval=c.interval,
                last_charge_on=c.last_charge_on and c.last_charge_on.isoformat(),
                monthly=None if c.monthly is None else Pence(minor=display(c.monthly)),
                cost_note=c.cost_note,
                counted=c.counted,
            )
            for c in s.items
        ],
        totals=[
            KindTotal(kind=k, monthly=Pence(minor=display(v)))
            for k, v in sorted(s.kind_totals.items())
        ],
        groups=[
            GroupTotal(kind=k, group=g, monthly=Pence(minor=display(v)))
            for (k, g), v in sorted(s.group_totals.items())
        ],
        rate_needed=s.rate_needed,
    )


@router.post("/{merchant_id}/cancel")
def cancel(led: Led, merchant_id: MerchantId) -> SubscriptionOut:
    """The user cancelled it: kept on the list, out of the totals."""
    return apply_change(led, merchant_id, lambda: led.cancel_subscription(merchant_id))
