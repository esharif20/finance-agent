"""A contextual Thompson-sampling bandit over Insights and playbook cards, in
shadow (#272, ADR 0015, docs/ML_EVALUATIONS.md). Pure functions.

**Arms and context.** An arm is an item with a type (an Insight's detector
type, or a card) and a Category. The context is the time of month (days 1-10,
11-20, 21 on) and recent spend against the user's usual (low, usual, high).
Each (type, Category, time of month, spend) cell has a Beta posterior:
Beta(1 + wins, 1 + losses), where its (type, Category) evidence from the other
buckets also counts at weight `POOL`, so a sparse cell starts from what the
item earned elsewhere.

**Reward** comes only from an explicit outcome (`REWARD`): accepted, acted on
or a regret review saying it was worth it is 1; rejected, dismissed or not
worth it is 0. An open, a click or time on screen is not an outcome and raises.

**Choosing.** One Beta draw per arm; the highest draws fill the slate. Its
size never exceeds the weekly `CAP`, whatever the bandit prefers. The draw is
seeded, so a seed always gives the same slate. Each item's propensity is its
probability of being in the slate, estimated from `DRAWS` further seeded draws
plus the slate's own (so a shown item's is never 0).

**Shadow.** `engine.insights.shown` asks the bandit what it would show and
logs that, with each live item's bandit probability, on the live exposures;
the live ranker keeps serving. Promotion is the user's decision, once
off-policy evaluation on real logs beats the live ranker.

Float is allowed here (`tests/test_no_float.py`): the draws are model
internals; every output is an item, a rank or a probability as a `Fraction`.
"""

import hashlib
import random
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

POLICY = "ts-shadow-v1"
CAP = 3  # items a week, whatever the bandit prefers
DRAWS = 99  # further draws for a propensity; with the slate's own, 100
POOL = 0.5  # weight of a (type, Category)'s evidence from its other buckets
SPEND_DAYS = 28  # recent spend: the last four weeks, against the 12 before
REWARD = {
    "accepted": 1,
    "acted": 1,
    "regret_worth_it": 1,
    "rejected": 0,
    "dismissed": 0,
    "regret_not_worth_it": 0,
}


def reward(outcome: str) -> int:
    """The reward of an explicit outcome; anything else (an open, a click,
    time on screen) is not a reward."""
    if outcome not in REWARD:
        raise ValueError(f"not a rewarded outcome: {outcome!r}")
    return REWARD[outcome]


@dataclass(frozen=True)
class Context:
    tom: int  # time of month: 0 days 1-10, 1 days 11-20, 2 from day 21
    spend: int  # recent spend: 0 low, 1 usual, 2 high


def time_of_month(day: date) -> int:
    return min((day.day - 1) // 10, 2)


def spend_level(spends: Iterable[tuple[date, int]], day: date) -> int:
    """Spend (signed pence, money out negative) in the `SPEND_DAYS` to `day`
    against the mean of the three periods before: under 80% is low, over 120%
    high. No usual spend is usual."""
    recent = usual = 0
    start = day - timedelta(SPEND_DAYS)
    for on, pence in spends:
        if start < on <= day:
            recent -= pence
        elif start - timedelta(3 * SPEND_DAYS) < on <= start:
            usual -= pence
    if usual <= 0:
        return 1
    # recent / (usual / 3) against 0.8 and 1.2, in integers
    return 0 if 15 * recent < 4 * usual else 2 if 5 * recent > 2 * usual else 1


def code(category: str) -> str:
    """A Category as a short code: the exposure context holds no text."""
    return hashlib.sha256(category.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class Arm:
    item: str
    type: str
    category: str


@dataclass(frozen=True)
class Observed:
    type: str
    category: str
    context: Context
    outcome: str


type Cell = tuple[str, str, int, int]


@dataclass(frozen=True)
class Model:
    cells: dict[Cell, tuple[int, int]]  # (wins, losses)
    pooled: dict[tuple[str, str], tuple[int, int]]

    def beta(self, arm: Arm, ctx: Context) -> tuple[float, float]:
        w, f = self.cells.get((arm.type, arm.category, ctx.tom, ctx.spend), (0, 0))
        pw, pf = self.pooled.get((arm.type, arm.category), (0, 0))
        return 1 + w + POOL * (pw - w), 1 + f + POOL * (pf - f)


def learn(seen: Iterable[Observed]) -> Model:
    """The posteriors from explicit outcomes only."""
    cells: dict[Cell, tuple[int, int]] = {}
    pooled: dict[tuple[str, str], tuple[int, int]] = {}
    for o in seen:
        r = reward(o.outcome)
        c = (o.type, o.category, o.context.tom, o.context.spend)
        w, f = cells.get(c, (0, 0))
        cells[c] = (w + r, f + 1 - r)
        w, f = pooled.get(c[:2], (0, 0))
        pooled[c[:2]] = (w + r, f + 1 - r)
    return Model(cells, pooled)


@dataclass(frozen=True)
class Pick:
    arm: Arm
    slot: int
    propensity: Fraction  # its probability of being in the slate


@dataclass(frozen=True)
class Decision:
    slate: list[Pick]
    membership: dict[str, Fraction]  # item -> probability of being in the slate


def _draw(
    arms: Sequence[Arm], model: Model, ctx: Context, n: int, rng: random.Random
) -> list[int]:
    theta = [rng.betavariate(*model.beta(a, ctx)) for a in arms]
    return sorted(range(len(arms)), key=lambda j: (-theta[j], j))[:n]


def choose(
    arms: Sequence[Arm],
    model: Model,
    ctx: Context,
    k: int,
    rng_seed: int,
    used: int = 0,
) -> Decision:
    """The bandit's slate of at most `k` arms, and never more than what is
    left of the week's `CAP` after `used` exposures."""
    n = max(0, min(k, CAP - used, len(arms)))
    rng = random.Random(rng_seed)  # noqa: S311 - exploration, not security
    first = _draw(arms, model, ctx, n, rng)
    hits = Counter(first)
    for _ in range(DRAWS if n else 0):
        hits.update(_draw(arms, model, ctx, n, rng))
    p = {j: Fraction(hits[j], DRAWS + 1) for j in range(len(arms))}
    return Decision(
        [Pick(arms[j], s, p[j]) for s, j in enumerate(first)],
        {arms[j].item: p[j] for j in range(len(arms))},
    )
