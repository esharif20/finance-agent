"""Plan's tools (#155, spec #134 item 10): the cash-flow forecast and a what-if
purchase, from `engine.forecast` and `engine.shortfall` exactly as
`/api/cash-flow/simulate-purchase` calls them. Figures are integer pence from
code. Read-only: a simulated purchase is never made, proposed or recorded, and
there is no payee or account field to put one in (no payments, ever)."""

import re
from collections.abc import Callable
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finance_agent.assistant.tools.read import Data, Money, NoData, Source
from finance_agent.engine import allocation as alloc
from finance_agent.engine.baselines import Insufficient
from finance_agent.engine.forecast import cash_flow
from finance_agent.engine.shortfall import Risk, outlook, pace
from finance_agent.ledger import CURRENCY, Ledger
from finance_agent.ledger.proposals import Memory, Proposals

_STRICT = ConfigDict(extra="forbid", frozen=True)
HORIZON = 3  # months, as the what-if endpoint
MAX_MINOR = 10**13  # as the what-if endpoint


def _gbp(minor: int) -> Money:
    return Money(minor=minor, currency=CURRENCY)


# --- forecast_cashflow -------------------------------------------------------


class ForecastCashflowIn(BaseModel):
    model_config = _STRICT
    months: int = Field(
        default=HORIZON, ge=1, le=6, description="how many months ahead"
    )


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
    amount_minor: int = Field(
        ge=1, le=MAX_MINOR, description="the price in pence: £50.00 is 5000"
    )
    on: date | None = Field(
        default=None, description="YYYY-MM-DD; default today, never in the past"
    )
    buffer_minor: int = Field(
        default=0,
        ge=0,
        le=MAX_MINOR,
        description="the least to keep spare, in pence: £50.00 is 5000",
    )


class SimulatePurchaseOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    status: Literal["ok", "insufficient"]
    purchase: Money  # the user's own figures, echoed
    buffer: Money
    on: date  # the purchase day, echoed
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
        on=when,
        payday=None if short else o.payday,
        minimum=None if short else _gbp(o.minimum),
        lowest_on=None if short else o.on,
        risk=None if short else o.risk,
        floor=None if short else _gbp(o.floor),
        have=o.have if short else None,
        need=o.need if short else None,
    )


# --- allocation_plan (#267) --------------------------------------------------

_FLOOR = re.compile(r"at least|minimum|no less|\bmin\b", re.IGNORECASE)
_PRIORITY_WEIGHT = {1: 5.0, 2: 4.0, 3: 3.0, 4: 2.0, 5: 1.0}
_Limits = dict[str, int]


class AllocationPlanIn(BaseModel):
    model_config = _STRICT


class AllocationLine(BaseModel):
    model_config = _STRICT
    kind: alloc.Kind
    name: Data  # a Category name or the user's own Memory text: quoted
    amount: Money
    reference: Money  # recent spending, goal target or bill amount
    reason: alloc.Reason


class AllocationPlanOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    status: Literal["ok", "infeasible", "insufficient"]
    month: str | None
    income: Money | None  # the month's expected recurring income
    lines: list[AllocationLine]  # the plan; empty unless ok
    baseline: list[AllocationLine]  # the 50/30/20 split of the same income
    infeasible_reason: str | None  # why no plan satisfies the hard limits


def _words(text: str) -> set[str]:
    return {w.removesuffix("s") for w in re.findall(r"[a-z0-9]+", text.lower())}


def _months_to(due: date, today: date) -> int:
    return max(1, (due.year - today.year) * 12 + due.month - today.month)


def _limits(
    memories: list[Memory], hit: Callable[[Memory], list[str]]
) -> tuple[_Limits, _Limits]:
    """Floors and caps from confirmed constraints with an amount, oldest first
    so the newest wins; `hit` names the lines a constraint is about."""
    floors: _Limits = {}
    caps: _Limits = {}
    for m in sorted(memories, key=lambda m: (m.said_on, m.created_at)):
        if m.type == "constraint" and m.amount_minor is not None:
            for name in hit(m):
                (floors if _FLOOR.search(m.text) else caps)[name] = m.amount_minor
    return floors, caps


def _lines(p: alloc.Plan) -> list[AllocationLine]:
    return [
        AllocationLine(
            kind=ln.kind,
            name=Data(data=ln.name),
            amount=_gbp(ln.amount_minor),
            reference=_gbp(ln.reference_minor),
            reason=ln.reason,
        )
        for ln in p.lines
    ]


def allocation_plan(src: Source, inp: AllocationPlanIn) -> AllocationPlanOut:
    """Next forecast month's expected income split across its fixed bills, the
    user's confirmed goals and the forecast Categories, under their confirmed
    constraints. Advice only: nothing is scheduled, moved or paid.

    shortcut: weights come from Memory priority only; learned preference
    strengths (#262/#269) are not stored yet, so `with_strengths` is not called
    here. Call it once they are. A constraint names a Category by its
    Subcategory words and a goal by a shared tag."""
    flow = cash_flow(Ledger(src.conn, src.user_id), src.today, 1)
    month = flow.months[0] if flow.months else None
    empty = AllocationPlanOut(
        evidence_id=f"allocation:{month.month if month else 'none'}",
        status="insufficient",
        month=month.month if month else None,
        income=None,
        lines=[],
        baseline=[],
        infeasible_reason=None,
    )
    if month is None or month.income <= 0:
        return empty
    memories = Proposals(src.conn, src.user_id).memories()
    cats = {
        f"{top} > {sub}": f.months[0].point
        for (top, sub), f in flow.categories.items()
        if f.months
    }
    cat_floors, cat_caps = _limits(
        memories,
        lambda m: [n for n in cats if _words(n.split(" > ")[1]) <= _words(m.text)],
    )
    goals = [
        (m, m.amount_minor, m.due_on)
        for m in memories
        if m.type == "goal" and m.amount_minor and m.due_on is not None
    ]
    names = {m.id: f"{m.text} #{m.id}" for m, _, _ in goals}
    goal_floors, goal_caps = _limits(
        memories,
        lambda c: [names[g.id] for g, _, _ in goals if set(g.tags) & set(c.tags)],
    )
    problem = alloc.AllocationInput(
        income_minor=month.income,
        bills=tuple(
            alloc.Bill(f"{r.merchant} ({r.interval})", r.months[0])
            for r in flow.recurring
            if r.months[0] > 0
        ),
        goals=tuple(
            alloc.Goal(
                names[m.id],
                -(-amount // _months_to(due, src.today)),
                goal_floors.get(names[m.id], 0),
                goal_caps.get(names[m.id]),
                _PRIORITY_WEIGHT.get(m.priority or 3, 3.0),
            )
            for m, amount, due in goals
        ),
        categories=tuple(
            alloc.Category(n, p, cat_floors.get(n, 0), cat_caps.get(n))
            for n, p in cats.items()
        ),
    )
    p = alloc.plan(problem)
    base = alloc.fifty_thirty_twenty(problem)
    bad = isinstance(p, alloc.Infeasible)
    return empty.model_copy(
        update={
            "status": "infeasible" if bad else "ok",
            "income": _gbp(month.income),
            "lines": [] if bad else _lines(p),
            "baseline": [] if isinstance(base, alloc.Infeasible) else _lines(base),
            "infeasible_reason": p.reason if bad else None,
        }
    )
