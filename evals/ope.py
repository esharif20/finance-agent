"""Off-policy evaluation (#261, #228, docs/ML_EVALUATIONS.md): do IPS and
self-normalised IPS, on the exposure log's propensities, recover a different
policy's true value? And why exploration exists: on the deterministic
ranker's log they cannot.

    uv run python evals/ope.py            # CI: results reproduce
    uv run python evals/ope.py --write    # refresh (see gate.py)

Synthetic users: `synthetic_history` histories, whose planted care for an
Insight's type and Category is the probability they accept it (hidden from
every policy). Each week a user gets `SLATE` candidate Insights, ranked by
impact x confidence, and the logging policy (`engine.ope.slate`) shows `K`.
The target policy keeps the same first `K - 1` and fills the last slot with
the most confident remaining candidate, so the two differ only there, and only
that slot is scored: its true value is the mean acceptance probability of the
target's item, known exactly from the plant.

Metrics per log: `ips_error` and `snips_error` (absolute, against the true
value), `ess` (effective sample size of the importance weights) and
`support` (the share of weeks in which the logging policy could have shown
the target's item). The deterministic log (epsilon 0) is the baseline: it
supports the target only in weeks where the two happen to agree.
"""

import random
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any

import gate
import synthetic_history as sh
from finance_agent.engine import ope, ranking
from ranking import Insight

RESULTS = Path(__file__).parent / "results" / "ope.json"
WEEKS = 2_000  # per user
SLATE = 10
K = 3
ERROR_BOUND = 0.06  # stated error: about two standard errors at the eps 0.1 ESS


def _candidates(h: sh.History, rng: random.Random) -> list[Insight]:
    cats = sorted(h.care_category)
    return [
        Insight(
            j,
            rng.choice(sh.EVENT_TYPES),
            rng.choice(cats),
            500 * 2 ** rng.randint(0, 7) + rng.randint(0, 499),
            rng.randint(50, 100),
        )
        for j in range(SLATE)
    ]


def _accept(h: sh.History, i: Insight) -> Fraction:
    return Fraction(h.care_type[i.type] * h.care_category[i.category], 10_000)


def _target(ranked: list[Insight]) -> int:
    """The target's last-slot rank: the most confident candidate below the
    first K - 1 (ties to the higher rank)."""
    rest = range(K - 1, len(ranked))
    return max(rest, key=lambda r: (ranked[r].confidence, -r))


def run(eps: Fraction) -> dict[str, float]:
    logs: list[ope.Logged] = []
    true_total = Fraction(0)
    supported = 0
    for h in sh.generate(sh.SEED):
        rng = random.Random(sh.SEED * 1_000 + 261 + h.id)  # noqa: S311 - synthetic
        for week in range(WEEKS):
            ranked = ranking.rank(_candidates(h, rng), {})
            want = _target(ranked)
            true_total += _accept(h, ranked[want])
            last = ope.slate(ranked, K, ope.seed(h.id, week, eps), eps)[-1]
            shown = ranked.index(last.item)
            supported += ope.propensities(len(ranked), K, eps)[-1].get(want, 0) > 0
            won = rng.randrange(10_000) < _accept(h, last.item) * 10_000
            logs.append(
                ope.Logged(Fraction(won), last.propensity, Fraction(shown == want))
            )
    truth = true_total / len(logs)
    est_ips = ope.ips(logs) or Fraction(0)
    est_snips = ope.snips(logs) or Fraction(0)
    return {
        "ips_error": round(float(abs(est_ips - truth)), 6),
        "snips_error": round(float(abs(est_snips - truth)), 6),
        "ess": round(float(ope.ess(logs)), 6),
        "support": round(supported / len(logs), 6),
        "true_value": round(float(truth), 6),
    }


MODELS = {
    "candidate": ("seeded epsilon slate (engine.ope)", ope.POLICY, ope.EPSILON),
    "baseline": ("deterministic ranker", "rank-v1", Fraction(0)),
}


def evaluate() -> dict[str, Any]:
    histories = sh.generate(sh.SEED)
    return {
        "harness": "ope",
        "dataset": "synthetic_history",
        "dataset_version": sh.DATASET_VERSION,
        "dataset_sha256": sh.fingerprint(histories),
        "seed": sh.SEED,
        "histories": len(histories),
        "weeks": WEEKS,
        "slate": SLATE,
        "shown": K,
        "error_bound": ERROR_BOUND,
        "models": {
            key: {"model": name, "model_version": version, "metrics": run(eps)}
            for key, (name, version, eps) in MODELS.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name.endswith("_error")


if __name__ == "__main__":
    sys.exit(gate.run("ope", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
