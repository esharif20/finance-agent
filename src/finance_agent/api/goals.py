"""Coaching goals (#278, spec #251 decision 9): each confirmed goal Memory with
its weekly target, progress and at-risk state from `engine.coaching`, plus the
playbook's payday check-in on a projected payday. Read-only: nothing here
moves, schedules or pays anything, and nothing logs."""

from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from finance_agent.api.guidance import Conn
from finance_agent.assistant.tools.guidance import evaluate
from finance_agent.assistant.tools.plan import AllocationPlanIn, allocation_plan
from finance_agent.assistant.tools.read import Source
from finance_agent.engine import coaching
from finance_agent.engine.forecast import cash_flow
from finance_agent.engine.playbook import choose_tone
from finance_agent.identity import current_user
from finance_agent.ledger import Ledger
from finance_agent.ledger.proposals import Memory, Proposals

router = APIRouter(prefix="/api/goals")
On = Annotated[date | None, Query()]  # the day to answer for; today when left out
CHECKIN = "payday-checkin"
DAYS_PER_MONTH = 30


class GoalOut(BaseModel):
    id: int
    text: str
    amount_minor: int
    due_on: date
    started_on: date
    linked_account_id: int | None
    status: coaching.Status
    estimated: bool  # no linked Account: progress is an estimate
    saved_minor: int
    remaining_minor: int
    weeks_left: int | None
    weekly_target_minor: int | None
    planned_weekly_minor: int | None
    shortfall_minor: int | None


class CheckinOut(BaseModel):
    title: str
    body: str


class GoalsOut(BaseModel):
    goals: list[GoalOut]
    payday: bool  # today is a projected payday
    checkin: CheckinOut | None  # only on a payday, in the user's tone


def _pence(amount: Decimal) -> int:
    return int(amount.scaleb(2))


def _saved(led: Ledger, m: Memory, plan_monthly: int | None, today: date) -> int:
    if m.linked_account_id is not None:
        return coaching.linked_saved(
            0,
            sum(
                _pence(t.amount)
                for t in led.transactions()
                if t.account_id == m.linked_account_id and t.posted_on >= m.said_on
            ),
        )
    # shortcut: the plan's goal line prorated by days elapsed, with overspend not
    # yet netted off (actual 0); refine when discretionary actuals are exposed
    planned = (plan_monthly or 0) * (today - m.said_on).days // DAYS_PER_MONTH
    return coaching.estimated_saved(planned, 0)


@router.get("")
def goals(conn: Conn, on: On = None) -> GoalsOut:
    today = on or date.today()
    user = current_user()
    src = Source(conn, user, today)
    led = Ledger(conn, user)
    memories = Proposals(conn, user).memories()
    plan = allocation_plan(src, AllocationPlanIn())
    lines = {ln.name.data: ln.amount.minor for ln in plan.lines}
    out: list[GoalOut] = []
    for m in memories:
        if m.type != "goal" or not m.amount_minor or m.due_on is None:
            continue
        if m.due_on <= m.said_on or today < m.said_on:
            continue  # no whole week to measure
        monthly = lines.get(f"{m.text} #{m.id}")
        t = coaching.weekly_target(
            m.amount_minor,
            m.said_on,
            m.due_on,
            today,
            _saved(led, m, monthly, today),
            estimated=m.linked_account_id is None,
            plan_monthly_minor=monthly,
        )
        out.append(
            GoalOut(
                id=m.id,
                text=m.text,
                amount_minor=m.amount_minor,
                due_on=m.due_on,
                started_on=m.said_on,
                linked_account_id=m.linked_account_id,
                status=t.status,
                estimated=t.estimated,
                saved_minor=t.saved_minor,
                remaining_minor=t.remaining_minor,
                weeks_left=t.weeks_left,
                weekly_target_minor=t.weekly_target_minor,
                planned_weekly_minor=t.planned_weekly_minor,
                shortfall_minor=t.shortfall_minor,
            )
        )
    flow = cash_flow(led, today, 1)
    payday = any(today in p.dates for p in flow.income)
    checkin = None
    if payday:
        shown = evaluate(src, choose_tone(memories)).shown  # pyright: ignore[reportArgumentType]
        card = next((s for s in shown if s.card_id == CHECKIN), None)
        checkin = card and CheckinOut(title=card.title, body=card.body)
    return GoalsOut(goals=out, payday=payday, checkin=checkin)
