"""Allocation evaluation (#260): the committed results reproduce from the seed,
the planner meets its hard gates and beats the 50/30/20 baseline."""

import json

import allocation
import gate
import synthetic_allocation as sa


def test_dataset_has_enough_seeded_scenarios_of_both_kinds() -> None:
    ss = sa.scenarios()
    assert len(ss) >= 200
    assert sa.fingerprint(ss) == sa.fingerprint(sa.scenarios())
    assert 0 < sum(s.feasible for s in ss) < len(ss)


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "allocation",
            allocation.evaluate,
            allocation.RESULTS,
            allocation.lower_is_better,
            [],
            1e-6,
        )
        == 0
    )


def test_planner_meets_its_gates_and_beats_the_baseline() -> None:
    models = json.loads(allocation.RESULTS.read_text())["models"]
    lp, base = models["allocation"]["metrics"], models["fifty_thirty_twenty"]["metrics"]
    assert lp["feasibility_rate"] == 1.0
    assert lp["infeasible_reported"] == 1.0
    assert lp["constraint_violations"] == 0
    assert lp["sum_exact"] == 1.0
    assert lp["oracle_utility"] > base["oracle_utility"]
    assert lp["deviation_from_recent"] < base["deviation_from_recent"]
    assert lp["instability"] <= 1.05
