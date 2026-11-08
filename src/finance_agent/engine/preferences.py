"""Pairwise preferences (#255, spec #251, docs/AI_DESIGN.md, ADR 0015):
Bradley-Terry strengths over items (Categories for "which would you cut
first?", playbook cards or goals for "which matters more?") and an
active-learning picker for the next question.

The model: P(a beats b) = sigmoid(s_a - s_b). Strengths are the MAP estimate
under a Gaussian prior N(prior_mean, PRIOR_SD^2) per item, found by Newton's
method (the log posterior is strictly concave, so it is unique and finite even
when one item wins every comparison). The caller decides what "beats" means
for its question; for "which would you cut first?" the winner is the item
kept.

The picker scores each pair by the information an answer would add under the
Laplace approximation, 1/2 log(1 + p(1 - p) v), where p is the predicted win
probability and v the posterior variance of s_a - s_b. It never picks a pair
already answered unless every pair is.

Floats are allowed in this module only (docs/ARCHITECTURE.md, #252); the
outputs are strengths and probabilities, never money. ADR 0015: the model
starts from its prior with zero answers and needs no gate; the prior mean
can come from spending (`prior_from_spend`). Pure functions:
no storage or API here (#262).
"""

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np

# Both tuned on evals/synthetic_preferences.py v1 (#255): a prior that keeps
# early fits calibrated, and spending as a loose hint of what matters
PRIOR_SD = 1.0
SPEND_WEIGHT = 0.5
_TOL = 1e-10
_MAX_STEPS = 100


@dataclass(frozen=True)
class Answer:
    winner: str
    loser: str


@dataclass(frozen=True)
class Posterior:
    items: tuple[str, ...]
    mean: tuple[float, ...]
    cov: tuple[tuple[float, ...], ...]  # Laplace approximation

    def strengths(self) -> dict[str, float]:
        return dict(zip(self.items, self.mean, strict=True))


def prior_from_spend(spend: Mapping[str, int]) -> dict[str, float]:
    """Prior means from monthly spend in pence: SPEND_WEIGHT times log spend,
    centred over the items with spend. An item with none gets 0 (no hint)."""
    if any(p < 0 for p in spend.values()):
        raise ValueError("spend must not be negative")
    logs = {item: math.log(p) for item, p in spend.items() if p > 0}
    centre = sum(logs.values()) / len(logs) if logs else 0.0
    return {
        item: SPEND_WEIGHT * (logs[item] - centre) if item in logs else 0.0
        for item in spend
    }


def _index(items: Sequence[str]) -> dict[str, int]:
    index = {item: k for k, item in enumerate(items)}
    if len(index) != len(items):
        raise ValueError("duplicate items")
    return index


def _wins(index: Mapping[str, int], answers: Iterable[Answer]) -> np.ndarray:
    """wins[i, j]: times i beat j. Counting first makes the fit independent of
    the order the answers came in, exactly."""
    counts = Counter[tuple[int, int]]()
    for a in answers:
        if a.winner not in index or a.loser not in index:
            raise ValueError("answer names an unknown item")
        if a.winner == a.loser:
            raise ValueError("an item cannot beat itself")
        counts[index[a.winner], index[a.loser]] += 1
    wins = np.zeros((len(index), len(index)))
    for (i, j), c in counts.items():
        wins[i, j] = c
    return wins


def posterior(
    items: Sequence[str],
    answers: Iterable[Answer],
    prior_mean: Mapping[str, float] | None = None,
    prior_sd: float = PRIOR_SD,
) -> Posterior:
    """MAP strengths and their Laplace covariance."""
    if not prior_sd > 0:
        raise ValueError("prior_sd must be positive")
    index = _index(items)
    wins = _wins(index, answers)
    n = len(items)
    mu = np.array([(prior_mean or {}).get(item, 0.0) for item in items])
    if not np.all(np.isfinite(mu)):
        raise ValueError("prior means must be finite")
    games = wins + wins.T
    precision = 1.0 / prior_sd**2
    s = mu.copy()
    hess = np.eye(n) * precision
    for _ in range(_MAX_STEPS):
        p = 1.0 / (1.0 + np.exp(-(s[:, None] - s[None, :])))  # p[i, j] = P(i beats j)
        grad = (wins - games * p).sum(axis=1) - precision * (s - mu)
        w = games * p * (1.0 - p)
        hess = np.diag(w.sum(axis=1)) - w + np.eye(n) * precision  # -Hessian
        step = np.linalg.solve(hess, grad)
        s = s + step
        if float(np.max(np.abs(step), initial=0.0)) < _TOL:
            break
    cov = np.linalg.inv(hess)
    return Posterior(
        tuple(items),
        tuple(float(x) for x in s),
        tuple(tuple(float(x) for x in row) for row in cov),
    )


def strengths(
    items: Sequence[str],
    answers: Iterable[Answer],
    prior_mean: Mapping[str, float] | None = None,
    prior_sd: float = PRIOR_SD,
) -> dict[str, float]:
    return posterior(items, answers, prior_mean, prior_sd).strengths()


def win_probability(fitted: Mapping[str, float], a: str, b: str) -> float:
    """P(a beats b) under fitted strengths."""
    return 1.0 / (1.0 + math.exp(fitted[b] - fitted[a]))


def information_gain(post: Posterior, a: str, b: str) -> float:
    i, j = post.items.index(a), post.items.index(b)
    p = 1.0 / (1.0 + math.exp(post.mean[j] - post.mean[i]))
    v = post.cov[i][i] + post.cov[j][j] - 2 * post.cov[i][j]
    return 0.5 * math.log1p(p * (1 - p) * max(v, 0.0))


def next_pair(
    items: Sequence[str],
    answers: Sequence[Answer],
    prior_mean: Mapping[str, float] | None = None,
    prior_sd: float = PRIOR_SD,
) -> tuple[str, str] | None:
    """The unanswered pair with the highest expected information gain (every
    pair once all are answered); ties go to the earliest pair in item order.
    None with fewer than two items."""
    post = posterior(items, answers, prior_mean, prior_sd)
    asked = {frozenset((a.winner, a.loser)) for a in answers}
    pairs = list(combinations(items, 2))
    fresh = [p for p in pairs if frozenset(p) not in asked]
    best: tuple[str, str] | None = None
    best_gain = -1.0
    for a, b in fresh or pairs:
        # rounded so last-digit platform arithmetic cannot reorder near-ties
        gain = round(information_gain(post, a, b), 9)
        if gain > best_gain:
            best, best_gain = (a, b), gain
    return best
