"""Pairwise preference evaluation (#255, docs/ML_EVALUATIONS.md): how well do
Bradley-Terry strengths, fitted from a few answers, recover a synthetic
user's latent utilities, and does the active picker learn faster than
random questions?

    uv run python evals/preferences.py            # CI: results reproduce
    uv run python evals/preferences.py --write    # refresh (see gate.py)

Metrics, per model, averaged over users (`synthetic_preferences`):

- `tau@<n>`: Kendall's tau-b of fitted strengths against latent utilities
  after n answers (a user with no ranking yet scores 0).
- `log_loss@<n>`: mean log loss on the user's held-out pairs (lower is better).
- `ece@<n>`: expected calibration error of those predictions, 10 equal-width
  bins (lower is better).

Models: `bt_active` (the engine with its picker, its prior mean from
spending), and two baselines it must beat at 10 answers: `bt_random` (the
same model and prior, random unanswered pairs: the picker's baseline) and
`spend_share` (rank by spending share, with P(a beats b) = spend_a /
(spend_a + spend_b): the model's baseline; it uses no answers, so its scores
repeat at every n).
"""

import math
import random
import sys
from collections.abc import Callable, Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

import gate
import synthetic_preferences as sp
from finance_agent.engine import preferences as pref

RESULTS = Path(__file__).parent / "results" / "preferences.json"
CHECKPOINTS = (
    5,
    10,
    20,
)  # metrics are rounded to 6 places, so the run reproduces exactly
BINS = 10

type Picker = Callable[[sp.User, list[pref.Answer]], tuple[str, str]]


def kendall_tau(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall's tau-b; 0 when either side is constant."""
    conc = disc = tx = ty = 0
    for i, j in combinations(range(len(x)), 2):
        dx, dy = x[i] - x[j], y[i] - y[j]
        if dx == 0 and dy == 0:
            continue
        if dx == 0:
            tx += 1
        elif dy == 0:
            ty += 1
        elif (dx > 0) == (dy > 0):
            conc += 1
        else:
            disc += 1
    denom = math.sqrt((conc + disc + tx) * (conc + disc + ty))
    return (conc - disc) / denom if denom else 0.0


def log_loss(preds: Sequence[tuple[float, bool]]) -> float:
    eps = 1e-12
    return -sum(
        math.log(min(max(p if won else 1 - p, eps), 1.0)) for p, won in preds
    ) / len(preds)


def ece(preds: Sequence[tuple[float, bool]]) -> float:
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(BINS)]
    for p, won in preds:
        bins[min(int(p * BINS), BINS - 1)].append((p, won))
    return sum(
        abs(sum(p for p, _ in b) - sum(w for _, w in b)) for b in bins if b
    ) / len(preds)


def _active(user: sp.User, answers: list[pref.Answer]) -> tuple[str, str]:
    pair = pref.next_pair(user.items, answers, pref.prior_from_spend(user.spend))
    if pair is None:
        raise ValueError("a user needs two items")
    return pair


def _random(seed: int) -> Picker:
    rng = random.Random(seed)  # noqa: S311 - synthetic evaluation, not security

    def pick(user: sp.User, answers: list[pref.Answer]) -> tuple[str, str]:
        asked = {frozenset((a.winner, a.loser)) for a in answers}
        fresh = [p for p in combinations(user.items, 2) if frozenset(p) not in asked]
        return rng.choice(fresh)

    return pick


def _scores(
    user: sp.User, fitted: Mapping[str, float], prob: Callable[[str, str], float]
) -> tuple[float, list[tuple[float, bool]]]:
    tau = kendall_tau(
        [fitted[i] for i in user.items], [user.utility[i] for i in user.items]
    )
    return tau, [(prob(a, b), won) for a, b, won in user.held_out]


def evaluate() -> dict[str, Any]:
    users = sp.users()
    keys = ("bt_active", "bt_random", "spend_share")
    taus = {k: {n: list[float]() for n in CHECKPOINTS} for k in keys}
    preds = {k: {n: list[tuple[float, bool]]() for n in CHECKPOINTS} for k in keys}
    for k, user in enumerate(users):
        spend = {i: float(p) for i, p in user.spend.items()}
        tau, ps = _scores(user, spend, lambda a, b, s=spend: s[a] / (s[a] + s[b]))
        for n in CHECKPOINTS:
            taus["spend_share"][n].append(tau)
            preds["spend_share"][n] += ps
        for key, picker in (("bt_active", _active), ("bt_random", _random(k))):
            answers: list[pref.Answer] = []
            while len(answers) < CHECKPOINTS[-1]:
                a, b = picker(user, answers)
                won = user.answer(a, b)
                answers.append(pref.Answer(a, b) if won else pref.Answer(b, a))
                if len(answers) in CHECKPOINTS:
                    s = pref.strengths(
                        user.items, answers, pref.prior_from_spend(user.spend)
                    )
                    tau, ps = _scores(
                        user, s, lambda a, b, s=s: pref.win_probability(s, a, b)
                    )
                    taus[key][len(answers)].append(tau)
                    preds[key][len(answers)] += ps

    def metrics(key: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for n in CHECKPOINTS:
            out[f"tau@{n}"] = round(sum(taus[key][n]) / len(users), 6)
            out[f"log_loss@{n}"] = round(log_loss(preds[key][n]), 6)
            out[f"ece@{n}"] = round(ece(preds[key][n]), 6)
        return out

    names = {
        "bt_active": (
            "engine.preferences Bradley-Terry + active picker",
            "bt-map-spend-prior-v1",
        ),
        "bt_random": ("engine.preferences Bradley-Terry + random pairs", "baseline"),
        "spend_share": ("rank by spending share", "baseline"),
    }
    return {
        "harness": "preferences",
        "dataset": "synthetic_preferences",
        "dataset_version": sp.DATASET_VERSION,
        "dataset_sha256": sp.fingerprint(users),
        "users": len(users),
        "prior_sd": pref.PRIOR_SD,
        "spend_weight": pref.SPEND_WEIGHT,
        "models": {
            key: {"model": model, "model_version": version, "metrics": metrics(key)}
            for key, (model, version) in names.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name.startswith(("log_loss", "ece"))


if __name__ == "__main__":
    sys.exit(
        gate.run(
            "preferences",
            evaluate,
            RESULTS,
            lower_is_better,
            sys.argv[1:],
        )
    )
