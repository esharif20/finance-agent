"""Seeded exploration and off-policy evaluation (#261, #228, spec #251
decision 1, ADR 0015, docs/ML_EVALUATIONS.md). Pure functions.

**The logging policy** (`POLICY`). The ranker's best `k` feasible items form
the slate. With probability `EPSILON`, its last slot is swapped for one of the
other feasible items, uniformly; nothing is ever added, so the slate never
grows past what the cap allows, and only feasible items are candidates. A
slate of one, or one with no other candidates, never explores, so the top
item is always the ranker's. The draw is seeded (per user, week and policy),
so the week's slate is stable and re-listing it shows nothing new.

**Propensities** are exact `Fraction`s: every slot but the last is shown with
probability 1; the last shows its ranked item with 1 - EPSILON and each other
candidate with EPSILON / (n - k).

**Estimators.** IPS: mean of reward x target / logged propensity; SNIPS
divides by the summed weights instead; ESS = (sum w)^2 / sum w^2. Exact
`Fraction`s, so the Engine holds no float (`tests/test_no_float.py`).
"""

import hashlib
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

POLICY = "rank-eps-v1"
EPSILON = Fraction(1, 10)


def propensities(n: int, k: int, eps: Fraction = EPSILON) -> list[dict[int, Fraction]]:
    """Per slot of a `k`-slate over `n` ranked candidates: rank -> the
    probability that rank is shown in that slot."""
    if n < 0 or k < 0 or not 0 <= eps <= 1:
        raise ValueError("n and k must be non-negative, eps in [0, 1]")
    k = min(k, n)
    out = [{s: Fraction(1)} for s in range(k)]
    if k >= 2 and n > k and eps:
        last = {k - 1: 1 - eps} if eps < 1 else {}
        out[-1] = last | {r: eps / (n - k) for r in range(k, n)}
    return out


@dataclass(frozen=True)
class Slot[T]:
    item: T
    slot: int
    propensity: Fraction


def seed(*parts: object) -> int:
    """A stable seed from its parts (Python's own hash is salted per run)."""
    blob = "\x1f".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big")


def slate[T](
    ranked: Sequence[T], k: int, rng_seed: int, eps: Fraction = EPSILON
) -> list[Slot[T]]:
    """The shown slate: `ranked[:k]` with its last slot explored as
    `propensities` says. `ranked` must already be feasible."""
    probs = propensities(len(ranked), k, eps)
    rng = random.Random(rng_seed)  # noqa: S311 - exploration, not security
    out: list[Slot[T]] = []
    for s, dist in enumerate(probs):
        pick = s
        if others := sorted(r for r in dist if r != s):
            explore = rng.randrange(eps.denominator) < eps.numerator
            pick = others[rng.randrange(len(others))] if explore else s
        out.append(Slot(ranked[pick], s, dist[pick]))
    return out


@dataclass(frozen=True)
class Logged:
    """One logged decision: the reward it earned, the probability the logging
    policy had of taking it, and the target policy's probability of it."""

    reward: Fraction
    propensity: Fraction
    target: Fraction


def _weights(logs: Iterable[Logged]) -> list[tuple[Fraction, Fraction]]:
    out: list[tuple[Fraction, Fraction]] = []
    for g in logs:
        if not 0 < g.propensity <= 1 or not 0 <= g.target <= 1:
            raise ValueError("propensity in (0, 1], target in [0, 1]")
        out.append((g.target / g.propensity, g.reward))
    return out


def ips(logs: Iterable[Logged]) -> Fraction | None:
    w = _weights(logs)
    return sum((x * r for x, r in w), Fraction(0)) / len(w) if w else None


def snips(logs: Iterable[Logged]) -> Fraction | None:
    w = _weights(logs)
    total = sum((x for x, _ in w), Fraction(0))
    return sum((x * r for x, r in w), Fraction(0)) / total if total else None


def ess(logs: Iterable[Logged]) -> Fraction:
    w = [x for x, _ in _weights(logs)]
    sq = sum((x * x for x in w), Fraction(0))
    return sum(w, Fraction(0)) ** 2 / sq if sq else Fraction(0)
