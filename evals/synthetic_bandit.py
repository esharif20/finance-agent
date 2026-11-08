"""Seeded synthetic users for the contextual bandit evaluation (#272,
docs/ML_EVALUATIONS.md). Synthetic only: Categories are generic labels.

Each user has a planted acceptance probability for every (item type,
Category) and a context effect on it: each type has a time of month the user
is most receptive to it (x1.6 there, x0.7 elsewhere), and a spend sensitivity
s in {-1, 0, 1} (x(1 + 0.5 s (spend - 1)): some items land better when spend
runs high, some when low). Probabilities are clipped to [0.01, 0.95].

Each of `WEEKS` weeks has a context (the time of month of its Monday and a
recent-spend level that drifts) and `CANDIDATES` items with a type, a
Category, an impact (pence a year, unrelated to acceptance) and a confidence.
Every item carries one pre-drawn uniform: it is accepted, if shown, when that
draw is under its probability, so every policy meets the same luck.
"""

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, timedelta

from finance_agent.engine.bandit import Context, time_of_month

DATASET_VERSION = 1
SEED = 272
USERS = 120
WEEKS = 26
CANDIDATES = 8
START = date(2026, 1, 5)  # a Monday
TYPES = ("recurring", "price_increase", "category_spike", "upcoming_renewal", "card")
CATEGORIES = tuple(f"cat-{k}" for k in range(1, 6))


@dataclass(frozen=True)
class Item:
    id: str
    type: str
    category: str
    impact: int  # pence a year
    confidence: int  # percent
    p: float  # planted acceptance probability in this week's context
    luck: float  # accepted when shown iff luck < p


@dataclass(frozen=True)
class Week:
    context: Context
    items: tuple[Item, ...]


@dataclass(frozen=True)
class User:
    name: str
    weeks: tuple[Week, ...]


def _p(base: float, best_tom: int, sens: int, ctx: Context) -> float:
    tom = 1.6 if ctx.tom == best_tom else 0.7
    return min(0.95, max(0.01, base * tom * (1 + 0.5 * sens * (ctx.spend - 1))))


def users() -> list[User]:
    out: list[User] = []
    for k in range(USERS):
        rng = random.Random(SEED * 1000 + k)  # noqa: S311 - synthetic data
        base = {(t, c): rng.uniform(0.05, 0.45) for t in TYPES for c in CATEGORIES}
        best = {t: rng.randrange(3) for t in TYPES}
        sens = {t: rng.choice((-1, 0, 1)) for t in TYPES}
        spend = 1
        weeks: list[Week] = []
        for w in range(WEEKS):
            spend = min(2, max(0, spend + rng.choice((-1, 0, 0, 1))))
            ctx = Context(time_of_month(START + timedelta(7 * w)), spend)
            items: list[Item] = []
            for j in range(CANDIDATES):
                t, c = rng.choice(TYPES), rng.choice(CATEGORIES)
                items.append(
                    Item(
                        f"w{w}-{j}",
                        t,
                        c,
                        500 * 2 ** rng.randint(0, 7) + rng.randint(0, 499),
                        rng.randint(50, 100),
                        _p(base[t, c], best[t], sens[t], ctx),
                        rng.random(),
                    )
                )
            weeks.append(Week(ctx, tuple(items)))
        out.append(User(f"user-{k:03d}", tuple(weeks)))
    return out


def fingerprint(us: list[User]) -> str:
    blob = [
        [
            u.name,
            [
                [w.context.tom, w.context.spend]
                + [
                    [
                        i.type,
                        i.category,
                        i.impact,
                        i.confidence,
                        round(i.p, 9),
                        round(i.luck, 9),
                    ]
                    for i in w.items
                ]
                for w in u.weeks
            ],
        ]
        for u in us
    ]
    return hashlib.sha256(json.dumps(blob).encode()).hexdigest()
