"""Coaching goals (#274, spec #251 decision 9): turn a goal Memory (amount,
due date, optional linked Account) into a weekly target that recomputes from
progress, and check it against the Allocation Plan's goal line.

Pure, derived on read: the caller passes the progress it measured.
- With a linked Account or Pot, progress is that Account's balance change
  since the goal started (`linked_saved`).
- Without one, progress is "estimated": planned minus actual discretionary
  spend since the goal started (`estimated_saved`), and the result says so.

Observe only: nothing here moves, schedules or pays anything. Integer pence.
The weekly target is the smallest whole-penny amount that, paid every week
left, reaches the goal by its due date. A past (or today's) due date is
`OVERDUE`, never a figure.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

WEEKS_PER_YEAR = 52
MONTHS_PER_YEAR = 12


class Status(StrEnum):
    REACHED = "reached"
    OVERDUE = "overdue"
    AT_RISK = "at_risk"  # the target needs more than the plan's goal line gives
    ON_TRACK = "on_track"
    UNCHECKED = "unchecked"  # no Allocation Plan line to check against


@dataclass(frozen=True)
class Target:
    status: Status
    estimated: bool  # progress from discretionary spend, not a linked Account
    saved_minor: int
    remaining_minor: int  # never negative
    weeks_left: int | None  # None when overdue or reached
    weekly_target_minor: int | None  # None when overdue; 0 when reached
    planned_weekly_minor: int | None  # the plan's goal line, per week
    shortfall_minor: int | None  # weekly target minus plan, when at risk


def linked_saved(balance_at_start_minor: int, balance_now_minor: int) -> int:
    """Progress from a linked Account: its balance change since the start."""
    return balance_now_minor - balance_at_start_minor


def estimated_saved(planned_minor: int, actual_minor: int) -> int:
    """Estimated progress: planned minus actual discretionary spend since the
    start (overspending counts against the goal)."""
    return planned_minor - actual_minor


def weekly_plan(monthly_minor: int) -> int:
    """A monthly Allocation Plan goal line as a weekly amount, rounded down so
    the check never credits money the plan does not set aside."""
    return monthly_minor * MONTHS_PER_YEAR // WEEKS_PER_YEAR


def weekly_target(
    amount_minor: int,
    started_on: date,
    due_on: date,
    today: date,
    saved_minor: int,
    *,
    estimated: bool,
    plan_monthly_minor: int | None = None,
) -> Target:
    if amount_minor <= 0:
        raise ValueError("a goal amount must be positive")
    if due_on <= started_on:
        raise ValueError("a goal is due after it starts")
    if today < started_on:
        raise ValueError("today is before the goal started")
    if plan_monthly_minor is not None and plan_monthly_minor < 0:
        raise ValueError("a plan line is never negative")
    remaining = max(0, amount_minor - saved_minor)
    planned = None if plan_monthly_minor is None else weekly_plan(plan_monthly_minor)

    def done(status: Status, target: int | None) -> Target:
        return Target(
            status, estimated, saved_minor, remaining, None, target, planned, None
        )

    if remaining == 0:
        return done(Status.REACHED, 0)
    if due_on <= today:
        return done(Status.OVERDUE, None)
    weeks = -(-(due_on - today).days // 7)  # a part week counts as a week
    target = -(-remaining // weeks)
    if planned is None:
        status, shortfall = Status.UNCHECKED, None
    elif target > planned:
        status, shortfall = Status.AT_RISK, target - planned
    else:
        status, shortfall = Status.ON_TRACK, None
    return Target(
        status, estimated, saved_minor, remaining, weeks, target, planned, shortfall
    )
