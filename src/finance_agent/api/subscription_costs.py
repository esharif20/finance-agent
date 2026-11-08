"""Subscription status, monthly cost and totals (#105), and the user's
"I cancelled it", for `current_user` only. Monthly figures are exact until
here, where each one is rounded half-even to the penny once. Waste flags and
free trials (#106) ride on each item; trial detection is partial."""

from datetime import date
from fractions import Fraction
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from finance_agent.api.subscriptions import (
    Led,
    MerchantId,
    SubscriptionOut,
    apply_change,
)
from finance_agent.categorise.subscriptions import (
    Flag,
    FlagCode,
    IntervalSource,
    costs,
    display,
)

router = APIRouter(prefix="/api/subscriptions")


class Pence(BaseModel):  # the shape of the main API's Money
    minor: int
    currency: str = "GBP"


class FlagOut(BaseModel):
    code: FlagCode
    on: str | None  # ISO date of the charge that raised it
    before: Pence | None  # price_rise only: the previous price
    after: Pence | None  # price_rise only: the new price


class TrialOut(BaseModel):
    started_on: str  # ISO date of the small first charge
    converts_on: str  # expected, from the service's usual trial length
    converted: bool


class CostOut(BaseModel):
    merchant_id: int
    merchant: str
    kind: str
    group: str
    status: str  # active, overdue, lapsed, cancelled or suggested
    interval: str | None
    # registry or detected; estimated (loose fit) or catalogue (known service)
    interval_source: IntervalSource | None
    charge_count: int  # GBP charges seen: 1 explains an interval_needed
    last_charge_on: str | None  # ISO date
    monthly: Pence | None  # None with a cost_note
    cost_note: str | None  # rate_needed, interval_needed or no_charge
    counted: bool  # in the totals: active or overdue, with a cost
    flags: list[FlagOut]
    trial: TrialOut | None  # best effort from posted charges: see trial_detection


class KindTotal(BaseModel):
    kind: str
    monthly: Pence


class GroupTotal(KindTotal):
    group: str


class Total(BaseModel):  # every counted item, each period rounded once
    monthly: Pence
    annual: Pence


class CostsOut(BaseModel):
    items: list[CostOut]
    total: Total
    totals: list[KindTotal]  # per kind: the exact sum of its counted items
    groups: list[GroupTotal]
    rate_needed: int  # items left out of totals until an FX rate exists
    flag_count: int  # every flag on every item, for the Overview card
    # posted charges show only a small first charge from a known service:
    # trials that start free stay unseen until the Mail signals (#80)
    trial_detection: Literal["partial"] = "partial"


def _pence(minor: int | None) -> Pence | None:
    return None if minor is None else Pence(minor=minor)


def _flag(f: Flag) -> FlagOut:
    return FlagOut(
        code=f.code,
        on=f.on and f.on.isoformat(),
        before=_pence(f.from_minor),
        after=_pence(f.to_minor),
    )


@router.get("/costs")
def subscription_costs(led: Led) -> CostsOut:
    s = costs(led, date.today())
    month = sum(s.kind_totals.values(), Fraction(0))
    return CostsOut(
        total=Total(
            monthly=Pence(minor=display(month)),
            annual=Pence(minor=display(month * 12)),
        ),
        items=[
            CostOut(
                merchant_id=c.entry.merchant_id,
                merchant=c.entry.merchant,
                kind=c.entry.kind or "",
                group=c.entry.group or "",
                status=c.status,
                interval=c.interval,
                interval_source=c.interval_source,
                charge_count=c.charge_count,
                last_charge_on=c.last_charge_on and c.last_charge_on.isoformat(),
                monthly=None if c.monthly is None else Pence(minor=display(c.monthly)),
                cost_note=c.cost_note,
                counted=c.counted,
                flags=[_flag(f) for f in c.flags],
                trial=c.trial
                and TrialOut(
                    started_on=c.trial.started_on.isoformat(),
                    converts_on=c.trial.converts_on.isoformat(),
                    converted=c.trial.converted,
                ),
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
        flag_count=sum(len(c.flags) for c in s.items),
    )


@router.post("/{merchant_id}/cancel")
def cancel(led: Led, merchant_id: MerchantId) -> SubscriptionOut:
    """The user cancelled it: kept on the list, out of the totals."""
    return apply_change(led, merchant_id, lambda: led.cancel_subscription(merchant_id))
