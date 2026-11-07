"""Personal baseline evaluation (#147, docs/ML_EVALUATIONS.md): how close each
baseline in `finance_agent.engine.baselines` comes to the truth on the
synthetic histories, against a trailing-mean baseline.

    uv run python evals/baselines.py            # CI: results reproduce
    uv run python evals/baselines.py --write    # refresh (see gate.py)

- **Seasonal.** Every discretionary Category-month where the ADR 0015 gate is
  met and no spike is planted: the expected spend against the generator's
  seasonal normal (`expected_spend`). Weighted absolute percentage error (WAPE:
  total absolute error over total truth), overall,
  per Category, and for the first year (before a same month last year exists)
  and after it.
- **Payday cycle.** Every discretionary Category and pay cycle where the gate
  is met: the spend expected in the cycle's first week against what was spent,
  WAPE again, which counts weeks with nothing spent, as Category spend is lumpy.
  The trailing-mean baseline spreads the month evenly over its days.

The trailing-mean baseline is the trivial spike rule's normal: the mean of the
three months before. Both are scored on the same Category-months.
"""

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import gate
import synthetic_history as sh
from finance_agent.engine.baselines import (
    MIN_CYCLES,
    MIN_MONTHS,
    Insufficient,
    payday_baseline,
    seasonal_baseline,
)

RESULTS = Path(__file__).parent / "results" / "baselines_baseline.json"
WEEK = 7


def paydays(h: sh.History) -> list[date]:
    """The day of each month's largest credit, as the Ledger shows it."""
    best: dict[int, sh.Txn] = {}
    for t in h.txns:
        m = sh.month_index(t.date)
        if t.amount > 0 and (m not in best or t.amount > best[m].amount):
            best[m] = t
    return sorted(t.date for t in best.values())


def _trailing(spend: dict[int, int], m: int) -> int:
    return sum(spend.get(m - i, 0) for i in (1, 2, 3)) // 3


def _add(acc: list[int], pred: int, truth: int) -> None:
    acc[0] += abs(pred - truth)
    acc[1] += truth


def evaluate() -> dict[str, Any]:
    histories = sh.generate(sh.SEED)
    # (total absolute error, total truth) in pence, per model and metric
    errors: defaultdict[str, defaultdict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    counts = {"seasonal": 0, "payday_week1": 0, "insufficient": 0}
    for h in histories:
        spiked = {(e.key, e.month) for e in h.events if e.type == "category_spike"}
        days = paydays(h)
        for cat in sh.DISCRETIONARY:
            rows = [(t.date, t.amount) for t in h.txns if t.category == cat]
            spend = {m: sh.month_spend(h, cat, m) for m in range(h.months)}
            for m in range(h.months):
                b = seasonal_baseline(rows, sh.month_start(m))
                if isinstance(b, Insufficient):
                    counts["insufficient"] += 1
                    continue
                if (sh.label(cat), m) in spiked:
                    continue
                truth = sh.expected_spend(h, cat, m)
                counts["seasonal"] += 1
                part = "after_year1" if b.seasonal else "year1"
                for name, pred in (
                    ("candidate", b.expected),
                    ("baseline", _trailing(spend, m)),
                ):
                    for key in ("all", part, sh.label(cat)):
                        _add(errors[name][f"seasonal.{key}"], pred, truth)
            for p in days:
                b = payday_baseline(rows, days, p)
                if isinstance(b, Insufficient):
                    continue
                week = p + timedelta(days=WEEK)
                truth = -sum(a for d, a in rows if p <= d < week and a < 0)
                counts["payday_week1"] += 1
                trailing = _trailing(spend, sh.month_index(p))
                for name, pred in (
                    ("candidate", b.spent_by(WEEK - 1)),
                    ("baseline", trailing * WEEK // 30),
                ):
                    _add(errors[name]["payday_week1.all"], pred, truth)
    names = {
        "baseline": ("trailing-3-month-mean", "1"),
        "candidate": ("personal-baselines", "1"),
    }
    return {
        "harness": "baselines",
        "dataset": "synthetic_history",
        "dataset_version": sh.DATASET_VERSION,
        "dataset_sha256": sh.fingerprint(histories),
        "seed": sh.SEED,
        "histories": len(histories),
        "months": sh.MONTHS,
        "gates": {"months": MIN_MONTHS, "cycles": MIN_CYCLES},
        "scored": counts,
        "models": {
            key: {
                "model": model,
                "model_version": version,
                "metrics": {
                    f"{k}.wape": round(err / truth, 6)
                    for k, (err, truth) in sorted(errors[key].items())
                },
            }
            for key, (model, version) in names.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name.endswith(".wape")


if __name__ == "__main__":
    sys.exit(gate.run("baselines", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
