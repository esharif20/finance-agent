# synthetic
"""Seeded goal histories (#274): a goal amount, start and due dates, a week
by week history of money put towards it, and the Allocation Plan's monthly
goal line. No real data.

Kinds: on_track, behind, ahead, missed_week (one week with nothing put by),
income_drop (the plan's goal line falls after an income drop), impossible
(due within two weeks with most still to find), overdue (due date passed)
and reached. Half the goals have a linked Account (progress is its balance
change); the rest are estimated from planned minus actual discretionary spend.
"""

import hashlib
import random
from dataclasses import dataclass
from datetime import date, timedelta

DATASET_VERSION = 1
SEED = 274
KINDS = (
    "on_track",
    "behind",
    "ahead",
    "missed_week",
    "income_drop",
    "impossible",
    "overdue",
    "reached",
)
PER_KIND = 30  # 240 scenarios
# money put by per week, as a percentage of the even weekly share
PACE = {"on_track": 100, "behind": 60, "ahead": 140, "missed_week": 100}


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    amount_minor: int
    started_on: date
    due_on: date
    today: date
    weekly_minor: tuple[int, ...]  # money put towards the goal each week
    linked: bool
    balance_at_start_minor: int  # linked Account
    planned_minor: int  # estimated: planned discretionary spend so far
    plan_monthly_minor: int  # the Allocation Plan's goal line


def _scenario(rng: random.Random, kind: str, i: int) -> Scenario:
    total = rng.randint(8, 52)
    amount = rng.randint(500, 5_000) * 100 + rng.randint(0, 99)
    share = -(-amount // total)
    start = date(2026, 1, 5) + timedelta(days=rng.randint(0, 120))
    due = start + timedelta(days=7 * total - rng.randint(0, 3))
    elapsed = rng.randint(1, total - 1)
    if kind == "impossible":
        elapsed = total - rng.randint(1, 2)
    weeks = [
        share * PACE.get(kind, 100) // 100 + rng.randint(-50, 50)
        for _ in range(elapsed)
    ]
    if kind == "missed_week":
        weeks[rng.randrange(elapsed)] = 0
    if kind == "impossible":
        weeks = [share * rng.randint(5, 30) // 100 for _ in range(elapsed)]
    if kind == "reached":
        weeks[-1] += amount
    today = start + timedelta(days=7 * elapsed + rng.randint(0, 6))
    if kind == "overdue":
        today = due + timedelta(days=rng.randint(0, 30))
    plan = -(-share * 52 // 12) * rng.randint(100, 125) // 100
    if kind == "income_drop":
        plan = plan * rng.randint(30, 60) // 100
    actual = rng.randint(20_000, 200_000)
    return Scenario(
        name=f"{kind}-{i}",
        kind=kind,
        amount_minor=amount,
        started_on=start,
        due_on=due,
        today=today,
        weekly_minor=tuple(weeks),
        linked=i % 2 == 0,
        balance_at_start_minor=rng.randint(0, 300_000),
        planned_minor=actual + sum(weeks),
        plan_monthly_minor=plan,
    )


def scenarios() -> list[Scenario]:
    rng = random.Random(SEED)  # noqa: S311 - synthetic data
    return [_scenario(rng, k, i) for k in KINDS for i in range(PER_KIND)]


def actual_minor(s: Scenario) -> int:
    """Actual discretionary spend so far: planned minus what was put by."""
    return s.planned_minor - sum(s.weekly_minor)


def balance_now_minor(s: Scenario) -> int:
    return s.balance_at_start_minor + sum(s.weekly_minor)


def fingerprint(ss: list[Scenario]) -> str:
    return hashlib.sha256(repr(ss).encode()).hexdigest()
