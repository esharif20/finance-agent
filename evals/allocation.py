"""Allocation Plan evaluation (#260, docs/ML_EVALUATIONS.md): is the plan
feasible, does it report infeasible mixes, and how does it compare with a
50/30/20 split of the same income and bills?

    uv run python evals/allocation.py            # CI: results reproduce
    uv run python evals/allocation.py --write    # refresh (see gate.py)

Metrics, per model, over the synthetic_allocation scenarios:

- `feasibility_rate`: feasible scenarios given a plan that breaks no hard
  constraint (must be 1.0 for the planner).
- `infeasible_reported`: infeasible scenarios answered `Infeasible`.
- `constraint_violations`: hard constraints broken in plans for feasible
  scenarios (lower is better; must be 0).
- `sum_exact`: plans whose lines sum to income to the penny.
- `oracle_utility`: 1 - sum w*|x - r| / (max w* x (spare + sum r)) under the
  planted true weights w*, where r is recent spending (goals: target); 0 for
  no plan or a plan that breaks a constraint. Mean over feasible scenarios.
- `deviation_from_recent`: sum |x - r| / income (lower is better).
- `instability`: sum of |line change| / |income change| when income moves
  by +-1% (lower is better; 1.0 moves only as much as income did).
"""

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import gate
import synthetic_allocation as sa
from finance_agent.engine import allocation as al

RESULTS = Path(__file__).parent / "results" / "allocation.json"
LOWER = {"constraint_violations", "deviation_from_recent", "instability"}
type Method = Callable[[al.AllocationInput], al.Plan | al.Infeasible]


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 6) if xs else 1.0


def _amounts(p: al.Plan) -> dict[tuple[al.Kind, str], int]:
    return {(ln.kind, ln.name): ln.amount_minor for ln in p.lines}


def _utility(s: sa.Scenario, p: al.Plan) -> float:
    true = dict(s.true_weights)
    flex = [ln for ln in p.lines if ln.kind in (al.Kind.GOAL, al.Kind.CATEGORY)]
    cost = sum(true[ln.name] * abs(ln.amount_minor - ln.reference_minor) for ln in flex)
    spare = p.income_minor - sum(
        ln.amount_minor for ln in p.lines if ln.kind is al.Kind.BILL
    )
    room = max(true.values(), default=1.0) * (
        spare + sum(ln.reference_minor for ln in flex)
    )
    return 1.0 - cost / room if room else 1.0


def _shifted(s: sa.Scenario, sign: int) -> al.AllocationInput:
    return replace(
        s.inp, income_minor=s.inp.income_minor + sign * (s.inp.income_minor // 100)
    )


def score(method: Method, ss: list[sa.Scenario]) -> dict[str, float]:
    feasible_ok: list[float] = []
    reported: list[float] = []
    violations = 0
    exact: list[float] = []
    utility: list[float] = []
    deviation: list[float] = []
    instability: list[float] = []
    for s in ss:
        r = method(s.inp)
        bad = al.violations(s.inp, r) if isinstance(r, al.Plan) else []
        if isinstance(r, al.Plan):
            exact.append(
                float(sum(ln.amount_minor for ln in r.lines) == r.income_minor)
            )
        if not s.feasible:
            reported.append(float(isinstance(r, al.Infeasible)))
            continue
        violations += len(bad)
        ok = isinstance(r, al.Plan) and not bad
        feasible_ok.append(float(ok))
        if not isinstance(r, al.Plan) or bad:
            utility.append(0.0)
            continue
        utility.append(_utility(s, r))
        flex = [ln for ln in r.lines if ln.kind in (al.Kind.GOAL, al.Kind.CATEGORY)]
        if s.inp.income_minor:
            deviation.append(
                sum(abs(ln.amount_minor - ln.reference_minor) for ln in flex)
                / s.inp.income_minor
            )
        step = s.inp.income_minor // 100
        if step:
            base = _amounts(r)
            for sign in (1, -1):
                moved = method(_shifted(s, sign))
                if isinstance(moved, al.Plan):
                    m = _amounts(moved)
                    instability.append(
                        sum(abs(m[k] - v) for k, v in base.items()) / step
                    )
    return {
        "feasibility_rate": _mean(feasible_ok),
        "infeasible_reported": _mean(reported),
        "constraint_violations": violations,
        "sum_exact": _mean(exact),
        "oracle_utility": _mean(utility),
        "deviation_from_recent": _mean(deviation),
        "instability": _mean(instability),
    }


def evaluate() -> dict[str, Any]:
    ss = sa.scenarios()
    names: dict[str, tuple[str, str, Method]] = {
        "allocation": ("engine.allocation linprog (HiGHS)", "lp-1", al.plan),
        "fifty_thirty_twenty": ("50/30/20 rule", "baseline", al.fifty_thirty_twenty),
    }
    return {
        "harness": "allocation",
        "dataset": "synthetic_allocation",
        "dataset_version": sa.DATASET_VERSION,
        "dataset_sha256": sa.fingerprint(ss),
        "scenarios": len(ss),
        "feasible_scenarios": sum(s.feasible for s in ss),
        "models": {
            key: {"model": model, "model_version": version, "metrics": score(fn, ss)}
            for key, (model, version, fn) in names.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name in LOWER


if __name__ == "__main__":
    # the LP solves in float: allow another platform's last digits (#149)
    sys.exit(
        gate.run("allocation", evaluate, RESULTS, lower_is_better, sys.argv[1:], 1e-6)
    )
