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

- **After a life change (#270).** On the planted shifts of
  `synthetic_life_changes` (#256), the six months from each shift: the seasonal
  baseline against the month's actual value, with a reset (candidate) and
  without (`no_reset`). A reset applies only from the month after
  `engine.changepoint` reports the shift (the user confirms it then, from the
  change month it names); an undetected shift, or a month before the report,
  scores the same in both. False alarms are taken as dismissed. The forecast
  check is the same reset on `forecast.forecast_category`, made the month after
  the report for the shift's remaining months: WAPE with and without.
"""

import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import gate
import synthetic_history as sh
import synthetic_life_changes as lc
from finance_agent.engine import changepoint as cp
from finance_agent.engine.baselines import (
    MIN_CYCLES,
    MIN_MONTHS,
    Insufficient,
    payday_baseline,
    seasonal_baseline,
)
from finance_agent.engine.forecast import forecast_category

RESULTS = Path(__file__).parent / "results" / "baselines_baseline.json"
WEEK = 7
AFTER = 6  # months scored from each planted shift
MAX_DELAY = 6  # a report this long after a shift is its detection, as #256

type Errors = defaultdict[str, list[int]]


def after_shift() -> tuple[dict[str, Errors], dict[str, int]]:
    """(errors per arm, counts) over the planted shifts' following months."""
    errors: dict[str, Errors] = {
        arm: defaultdict(lambda: [0, 0]) for arm in ("reset", "no_reset")
    }
    counts = {"shifts": 0, "detected": 0, "months": 0, "forecast_months": 0}
    for h in lc.generate(lc.SEED):
        for shift in h.shifts:
            if shift.type == "spike":
                continue
            counts["shifts"] += 1
            rows = h.rows[shift.series]
            _, values, _ = cp.monthly(rows, shift.series)
            sign = -1 if shift.series == "income" else 1  # income as money out
            pairs = [(d, sign * p) for _, d, p in rows]
            found = cp.detect_changes(rows, shift.series)
            reports = () if isinstance(found, Insufficient) else found
            hit = next(
                (
                    c
                    for c in reports
                    if 0 <= sh.month_index(c.detected) - shift.month <= MAX_DELAY
                ),
                None,
            )
            counts["detected"] += hit is not None
            for m in range(shift.month, min(shift.month + AFTER, h.months)):
                month = sh.month_start(m)
                plain = seasonal_baseline(pairs, month)
                reset = plain
                if hit is not None and sh.month_index(hit.detected) < m:
                    reset = seasonal_baseline(pairs, month, since=hit.month)
                if isinstance(plain, Insufficient) or isinstance(reset, Insufficient):
                    continue
                counts["months"] += 1
                for key in ("all", shift.type):
                    k = f"after_shift.{key}"
                    _add(errors["reset"][k], reset.expected, values[m])
                    _add(errors["no_reset"][k], plain.expected, values[m])
            if hit is None:
                continue
            made = sh.month_index(hit.detected) + 1
            horizon = min(shift.month + AFTER, h.months) - made
            if horizon < 1:
                continue
            change = sh.month_index(hit.month)
            arms = {
                "no_reset": forecast_category(values[:made], horizon),
                "reset": forecast_category(values[change:made], horizon, reset=True),
            }
            if any(not f.months for f in arms.values()):
                continue
            counts["forecast_months"] += horizon
            for arm, f in arms.items():
                for k, band in enumerate(f.months):
                    acc = errors[arm]["after_shift_forecast.all"]
                    _add(acc, band.point, values[made + k])
    return errors, counts


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
    shifted, shift_counts = after_shift()
    for name, arm in (("candidate", "reset"), ("no_reset", "no_reset")):
        errors[name].update(shifted[arm])
    names = {
        "baseline": ("trailing-3-month-mean", "1"),
        "candidate": ("personal-baselines", "1"),
        "no_reset": ("personal-baselines-no-reset", "1"),
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
        "life_changes": {
            "dataset": "synthetic_life_changes",
            "dataset_version": lc.DATASET_VERSION,
            "dataset_sha256": lc.fingerprint(lc.generate(lc.SEED)),
            "seed": lc.SEED,
            "scored": shift_counts,
        },
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
