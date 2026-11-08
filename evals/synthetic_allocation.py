# synthetic
"""Seeded Allocation Plan scenarios (#260): income, fixed bills, goals with
floors, caps and targets, Categories with recent spending, floors and caps,
and planted infeasible mixes. No real data.

Each scenario carries a planted utility oracle: true weights per goal and
Category. The input's weights are the coarse Memory priorities (1 to 3) a user
would state, so the planner sees only a rounded version of the truth.
`feasible` is planted by construction and checked in integers: bills within
income, every floor within its cap, and the floors within what bills leave.
"""

import hashlib
import random
from dataclasses import dataclass

from finance_agent.engine.allocation import AllocationInput, Bill, Category, Goal

DATASET_VERSION = 1
SEED = 260
KINDS = (
    "surplus",
    "shortfall",
    "tight_floors",
    "caps",
    "zero_income",
    "extreme",
    "infeasible_bills",
    "infeasible_floors",
    "cap_below_floor",
)
PER_KIND = 24  # 216 scenarios
CATEGORIES = (
    ("groceries", True),
    ("transport", True),
    ("utilities", True),
    ("eating_out", False),
    ("takeaway", False),
    ("shopping", False),
    ("entertainment", False),
)
GOALS = ("emergency_fund", "car", "holiday")


@dataclass(frozen=True)
class Scenario:
    name: str
    kind: str
    inp: AllocationInput
    true_weights: tuple[tuple[str, float], ...]  # line name -> planted weight
    feasible: bool


def _priority(w: float) -> float:
    return float(min(3, max(1, round(w))))


def _scenario(kind: str, i: int) -> Scenario:
    rng = random.Random(f"{SEED}-{kind}-{i}")  # noqa: S311 - synthetic data
    income = rng.randrange(150_000, 800_000)  # £1,500 to £8,000 a month
    if kind == "zero_income":
        income = 0
    if kind == "extreme":
        income = rng.randrange(10**11, 10**13)
    bills_total = income * rng.randrange(20, 55) // 100
    n_bills = rng.randrange(1, 5)
    bills = [
        Bill(f"bill_{j}", a) for j, a in enumerate(_parts(rng, bills_total, n_bills))
    ]
    if kind == "infeasible_bills":
        bills.append(Bill("rent_rise", income - bills_total + rng.randrange(1, 50_000)))
    spare = max(income - sum(b.amount_minor for b in bills), 0)

    cats_used = rng.sample(CATEGORIES, rng.randrange(2, len(CATEGORIES) + 1))
    goals_used = rng.sample(GOALS, rng.randrange(0, len(GOALS) + 1))
    share = {
        "surplus": rng.uniform(0.4, 0.9),
        "shortfall": rng.uniform(1.1, 1.6),
        "tight_floors": rng.uniform(1.2, 1.5),
        "caps": rng.uniform(0.8, 1.2),
    }.get(kind, rng.uniform(0.6, 1.3))
    refs = _parts(
        rng,
        int(max(spare, 100_000 if income else 0) * share),
        len(cats_used) + len(goals_used),
    )
    floor_share = 0.95 if kind == "tight_floors" else rng.uniform(0.0, 0.5)
    true: list[tuple[str, float]] = []
    goals: list[Goal] = []
    cats: list[Category] = []
    floor_budget = int(spare * floor_share)
    for name, ref in zip(goals_used, refs, strict=False):
        floor = min(ref * rng.randrange(0, 80) // 100, floor_budget)
        floor_budget -= floor
        w = rng.uniform(0.5, 3.0)
        true.append((name, w))
        goals.append(Goal(name, ref, floor, None, _priority(w)))
    for (name, essential), ref in zip(cats_used, refs[len(goals_used) :], strict=True):
        floor = (
            min(ref * rng.randrange(50, 100) // 100, floor_budget) if essential else 0
        )
        floor_budget -= floor
        cap = None
        if not essential and (kind == "caps" or rng.random() < 0.2):
            cap = ref * rng.randrange(40, 100) // 100
        w = rng.uniform(0.5, 3.0)
        true.append((name, w))
        cats.append(Category(name, ref, floor, cap, _priority(w), essential))
    if kind == "infeasible_floors":
        c = cats[0]
        cats[0] = Category(
            c.name,
            c.recent_minor,
            spare + rng.randrange(1, 50_000),
            None,
            c.weight,
            c.essential,
        )
    if kind == "cap_below_floor":
        c = cats[0]
        floor = max(c.floor_minor, 1)
        cats[0] = Category(
            c.name, c.recent_minor, floor, floor - 1, c.weight, c.essential
        )
    rng.shuffle(cats)
    rng.shuffle(goals)
    inp = AllocationInput(income, tuple(bills), tuple(goals), tuple(cats))
    return Scenario(f"{kind}-{i:02d}", kind, inp, tuple(true), _feasible(inp))


def _parts(rng: random.Random, total: int, n: int) -> list[int]:
    """`total` cut into `n` random non-negative integer parts."""
    if n <= 0:
        return []
    cuts = sorted(rng.randrange(0, total + 1) for _ in range(n - 1))
    edges = [0, *cuts, total]
    return [b - a for a, b in zip(edges, edges[1:], strict=False)]


def _feasible(inp: AllocationInput) -> bool:
    spare = inp.income_minor - sum(b.amount_minor for b in inp.bills)
    lines = [(g.floor_minor, g.cap_minor) for g in inp.goals] + [
        (c.floor_minor, c.cap_minor) for c in inp.categories
    ]
    return (
        spare >= 0
        and all(hi is None or lo <= hi for lo, hi in lines)
        and sum(lo for lo, _ in lines) <= spare
    )


def scenarios() -> list[Scenario]:
    return [_scenario(k, i) for k in KINDS for i in range(PER_KIND)]


def fingerprint(ss: list[Scenario]) -> str:
    return hashlib.sha256(repr(ss).encode()).hexdigest()
