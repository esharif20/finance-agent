"""Coaching goal evaluation (#274, docs/ML_EVALUATIONS.md): is the weekly
target exact, is it checked against the plan correctly, and how does it
compare with a fixed target set at the start?

    uv run python evals/coaching.py            # CI: results reproduce
    uv run python evals/coaching.py --write    # refresh (see gate.py)

The truth is a closed-form recomputation from the history, written apart from
the engine: remaining R = max(0, amount - saved), weeks left W = ceil(days/7),
target = (R + W - 1) // W; at risk when R > W * weekly plan line.

Metrics, per model, over the synthetic_goals scenarios:
- `target_exact`: target equals the truth to the penny (overdue: no figure).
- `feasibility_agreement`: open goals whose plan check matches the truth.
- `at_risk_precision`, `at_risk_recall`: of the "at risk" flags.
- `overdue_reported`: overdue goals answered with no figure.
- `negative_targets`: targets below zero (lower is better; must be 0).
"""

import sys
from pathlib import Path
from typing import Any

import gate
import synthetic_goals as sg
from finance_agent.engine import coaching as co

RESULTS = Path(__file__).parent / "results" / "coaching.json"
LOWER = {"negative_targets"}

type Answer = tuple[int | None, bool]  # (weekly target, at risk)


def truth(s: sg.Scenario) -> Answer:
    remaining = max(0, s.amount_minor - sum(s.weekly_minor))
    if remaining == 0:
        return 0, False
    days = (s.due_on - s.today).days
    if days <= 0:
        return None, False
    weeks = (days + 6) // 7
    capacity = s.plan_monthly_minor * 12 // 52
    return (remaining + weeks - 1) // weeks, remaining > weeks * capacity


def engine(s: sg.Scenario) -> Answer:
    saved = (
        co.linked_saved(s.balance_at_start_minor, sg.balance_now_minor(s))
        if s.linked
        else co.estimated_saved(s.planned_minor, sg.actual_minor(s))
    )
    t = co.weekly_target(
        s.amount_minor,
        s.started_on,
        s.due_on,
        s.today,
        saved,
        estimated=not s.linked,
        plan_monthly_minor=s.plan_monthly_minor,
    )
    return t.weekly_target_minor, t.status is co.Status.AT_RISK


def fixed(s: sg.Scenario) -> Answer:
    """Baseline: amount / weeks at the start, never recomputed."""
    weeks = -(-(s.due_on - s.started_on).days // 7)
    target = -(-s.amount_minor // weeks)
    return target, target > co.weekly_plan(s.plan_monthly_minor)


def _ratio(n: int, d: int) -> float:
    return round(n / d, 6) if d else 1.0


def score(method: Any, ss: list[sg.Scenario]) -> dict[str, float]:
    exact = agree = open_ = tp = fp = fn = overdue = overdue_ok = negative = 0
    for s in ss:
        (want, want_risk), (got, got_risk) = truth(s), method(s)
        exact += got == want
        negative += got is not None and got < 0
        if want is None:
            overdue += 1
            overdue_ok += got is None
            continue
        if want == 0:
            continue
        open_ += 1
        agree += got_risk == want_risk
        tp += got_risk and want_risk
        fp += got_risk and not want_risk
        fn += want_risk and not got_risk
    return {
        "target_exact": _ratio(exact, len(ss)),
        "feasibility_agreement": _ratio(agree, open_),
        "at_risk_precision": _ratio(tp, tp + fp),
        "at_risk_recall": _ratio(tp, tp + fn),
        "overdue_reported": _ratio(overdue_ok, overdue),
        "negative_targets": negative,
    }


def evaluate() -> dict[str, Any]:
    ss = sg.scenarios()
    names = {
        "coaching": ("engine.coaching weekly target", "coaching-1", engine),
        "fixed_target": ("amount / weeks at start", "baseline", fixed),
    }
    return {
        "harness": "coaching",
        "dataset": "synthetic_goals",
        "dataset_version": sg.DATASET_VERSION,
        "dataset_sha256": sg.fingerprint(ss),
        "scenarios": len(ss),
        "at_risk_scenarios": sum(truth(s)[1] for s in ss),
        "models": {
            key: {"model": model, "model_version": version, "metrics": score(fn, ss)}
            for key, (model, version, fn) in names.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name in LOWER


if __name__ == "__main__":
    sys.exit(gate.run("coaching", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
