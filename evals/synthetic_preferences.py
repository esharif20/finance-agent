"""Seeded synthetic users for the pairwise preference evaluation (#255,
docs/ML_EVALUATIONS.md). Synthetic only: the items are generic labels.

Each user has 12 to 20 items with latent utilities u ~ N(0, UTILITY_SD^2).
The answer model is Bradley-Terry with noise: the item with the higher
utility wins with probability sigmoid(u_a - u_b), and then 10% of answers
flip. Every unordered pair gets one fixed training answer, drawn up front, so
the active picker and the random picker meet the same answers for the same
questions. A separate set of held-out pairs, with their own independent
answers, scores log loss and calibration.

`spend` (pence a month per item) is the baseline's signal and the model's
prior: it tracks utility only loosely, log spend = SPEND_LINK * u + N(0, 1)
plus a constant, because what someone spends most on is not reliably what
matters most to them.
"""

import hashlib
import json
import math
import random
from dataclasses import dataclass
from itertools import combinations

DATASET_VERSION = 1
SEED = 255
USERS = 300
UTILITY_SD = 1.5
FLIP = 0.10
SPEND_LINK = 0.4
HELD_OUT = 40
ITEMS = tuple(f"item-{k:02d}" for k in range(1, 21))


@dataclass(frozen=True)
class User:
    name: str
    items: tuple[str, ...]
    utility: dict[str, float]
    spend: dict[str, int]  # pence a month
    answers: dict[tuple[str, str], bool]  # (a, b) in item order: True if a wins
    held_out: tuple[tuple[str, str, bool], ...]  # (a, b, a wins)

    def answer(self, a: str, b: str) -> bool:
        """Does a beat b? The user's fixed training answer for the pair."""
        return self.answers[a, b] if (a, b) in self.answers else not self.answers[b, a]


def _wins(rng: random.Random, ua: float, ub: float) -> bool:
    won = rng.random() < 1.0 / (1.0 + math.exp(ub - ua))
    return won != (rng.random() < FLIP)


def users() -> list[User]:
    out: list[User] = []
    for k in range(USERS):
        rng = random.Random(SEED * 1000 + k)  # noqa: S311 - synthetic data, not security
        items = tuple(rng.sample(ITEMS, rng.randint(12, 20)))
        u = {i: rng.gauss(0.0, UTILITY_SD) for i in items}
        spend = {
            i: max(1, round(5000 * math.exp(SPEND_LINK * u[i] + rng.gauss(0.0, 1.0))))
            for i in items
        }
        pairs = list(combinations(items, 2))
        answers = {(a, b): _wins(rng, u[a], u[b]) for a, b in pairs}
        held = tuple(
            (a, b, _wins(rng, u[a], u[b])) for a, b in rng.sample(pairs, HELD_OUT)
        )
        out.append(User(f"user-{k:03d}", items, u, spend, answers, held))
    return out


def fingerprint(us: list[User]) -> str:
    blob = [
        [
            u.name,
            list(u.items),
            [round(u.utility[i], 9) for i in u.items],
            [u.spend[i] for i in u.items],
            [int(w) for w in u.answers.values()],
            [list(h) for h in u.held_out],
        ]
        for u in us
    ]
    return hashlib.sha256(json.dumps(blob).encode()).hexdigest()
