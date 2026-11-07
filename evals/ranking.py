"""Ranking evaluation (#146, docs/ML_EVALUATIONS.md): NDCG@k of Insight slates
against a planted-relevance oracle, against ranking by £ impact alone.

    uv run python evals/ranking.py            # CI: results reproduce
    uv run python evals/ranking.py --write    # refresh (see gate.py)

Each synthetic user gets one slate of candidate Insights a month. The oracle's
gain for an Insight is its expected worth to that user: impact x confidence x
the user's planted care for its type and its Category (`History.care_type`,
`History.care_category`), hidden from rankers. After each round the user sees
the top `SHOWN` Insights of a fixed logging policy (£ impact) and rates each
one, liked with probability care_type x care_category; a `Ranker` gets every
earlier rating, as the Beta-Bernoulli relevance posterior (#148) will.
"""

import random
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import gate
import synthetic_history as sh
from metrics import ndcg_at_k

RESULTS = Path(__file__).parent / "results" / "ranking_baseline.json"
SLATE = 10
SHOWN = 3
ROUNDS = sh.MIN_MONTHS  # one slate a month
KS = (3, 5)


class Insight(NamedTuple):
    id: int
    type: str  # one of synthetic_history.EVENT_TYPES
    category: str  # Category label
    impact: int  # pence
    confidence: int  # percent


class Feedback(NamedTuple):
    type: str
    category: str
    liked: bool


class Ranker(Protocol):
    def __call__(
        self, slate: Sequence[Insight], feedback: Sequence[Feedback], /
    ) -> Sequence[Insight]: ...


def gain(h: sh.History, i: Insight) -> int:
    """The oracle: expected worth of the Insight to this user."""
    return i.impact * i.confidence * h.care_type[i.type] * h.care_category[i.category]


def _slates(h: sh.History) -> list[list[Insight]]:
    rng = random.Random(sh.SEED * 1_000 + 500 + h.id)  # noqa: S311 - synthetic data
    cats = sorted(h.care_category)
    out: list[list[Insight]] = []
    for r in range(ROUNDS):
        out.append(
            [
                Insight(
                    r * SLATE + j,
                    rng.choice(sh.EVENT_TYPES),
                    rng.choice(cats),
                    500 * 2 ** rng.randint(0, 7) + rng.randint(0, 499),
                    rng.randint(50, 100),
                )
                for j in range(SLATE)
            ]
        )
    return out


def score(h: sh.History, rank: Ranker) -> dict[str, float]:
    """Mean NDCG@k over the user's rounds."""
    rng = random.Random(sh.SEED * 1_000 + 900 + h.id)  # noqa: S311 - synthetic data
    feedback: list[Feedback] = []
    totals = dict.fromkeys(KS, 0.0)
    for slate in _slates(h):
        ranked = list(rank(list(slate), tuple(feedback)))
        if sorted(i.id for i in ranked) != sorted(i.id for i in slate):
            raise ValueError("a ranker must return a permutation of its slate")
        for k in KS:
            totals[k] += ndcg_at_k([gain(h, i) for i in ranked], k)
        for i in baseline(slate, ())[:SHOWN]:  # the logging policy
            p = h.care_type[i.type] * h.care_category[i.category]
            feedback.append(Feedback(i.type, i.category, rng.randrange(10_000) < p))
    return {f"ndcg@{k}": totals[k] / ROUNDS for k in KS}


def baseline(slate: Sequence[Insight], feedback: Sequence[Feedback]) -> list[Insight]:
    """Rank by £ impact alone (ties keep slate order)."""
    return sorted(slate, key=lambda i: -i.impact)


def stub(slate: Sequence[Insight], feedback: Sequence[Feedback]) -> list[Insight]:
    """Placeholder until #148: the slate as given."""
    return list(slate)


MODELS: dict[str, tuple[str, str, Ranker]] = {
    "baseline": ("impact", "1", baseline),
    "candidate": ("stub", "0", stub),
}


def evaluate() -> dict[str, Any]:
    histories = sh.generate(sh.SEED)
    models: dict[str, Any] = {}
    for key, (name, version, rank) in MODELS.items():
        per = {h.id: score(h, rank) for h in histories}
        metrics = {
            f"overall.ndcg@{k}": round(
                sum(s[f"ndcg@{k}"] for s in per.values()) / len(per), 6
            )
            for k in KS
        }
        for hid, s in per.items():
            metrics |= {f"history_{hid}.{m}": round(v, 6) for m, v in s.items()}
        models[key] = {"model": name, "model_version": version, "metrics": metrics}
    return {
        "harness": "ranking",
        "dataset": "synthetic_history",
        "dataset_version": sh.DATASET_VERSION,
        "dataset_sha256": sh.fingerprint(histories),
        "seed": sh.SEED,
        "histories": len(histories),
        "rounds": ROUNDS,
        "slate": SLATE,
        "shown": SHOWN,
        "models": models,
    }


def lower_is_better(name: str) -> bool:
    return False


if __name__ == "__main__":
    sys.exit(gate.run("ranking", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
