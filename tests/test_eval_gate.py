"""The shared results gate for the M5 harnesses (#146): CI demands committed
results equal a fresh run, and `--write` refuses to lower a committed score."""

import json
from pathlib import Path
from typing import Any

import gate


def _results(
    score: float, err: float = 0.5, sha: str = "a", version: int = 1
) -> dict[str, Any]:
    return {
        "dataset_version": version,
        "dataset_sha256": sha,
        "models": {
            "baseline": {
                "model": "rule",
                "model_version": "1",
                "metrics": {"overall.recall": score, "overall.mase": err},
            }
        },
    }


def _lower(name: str) -> bool:
    return name.endswith("mase")


def test_lowering_any_metric_is_reported_in_its_own_direction() -> None:
    old = _results(0.8)
    assert gate.lowered(_results(0.9, 0.4), old, _lower) == []
    assert len(gate.lowered(_results(0.7), old, _lower)) == 1
    assert len(gate.lowered(_results(0.8, 0.6), old, _lower)) == 1


def test_a_changed_dataset_needs_a_version_bump_and_models_cannot_vanish() -> None:
    old = _results(0.8)
    assert len(gate.lowered(_results(0.8, sha="b"), old, _lower)) == 1
    # a new dataset version starts a new bar
    assert gate.lowered(_results(0.1, sha="b", version=2), old, _lower) == []
    gone: dict[str, Any] = {**_results(0.8), "models": {}}
    assert len(gate.lowered(gone, old, _lower)) == 1
    no_metric = _results(0.8)
    del no_metric["models"]["baseline"]["metrics"]["overall.mase"]
    assert len(gate.lowered(no_metric, old, _lower)) == 1


def test_write_refuses_to_lower_the_committed_baseline(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_results(0.8)))
    worse = _results(0.7)
    assert gate.run("t", lambda: worse, path, _lower, ["--write"]) == 1
    assert json.loads(path.read_text()) == _results(0.8)  # untouched
    # explicit consent writes it, and the CI check then passes
    assert (
        gate.run("t", lambda: worse, path, _lower, ["--write", "--accept-lower"]) == 0
    )
    assert json.loads(path.read_text()) == worse
    assert gate.run("t", lambda: worse, path, _lower, []) == 0


def test_ci_fails_when_committed_results_are_not_a_fresh_run(tmp_path: Path) -> None:
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_results(0.8)))
    # even a better score must be written, not left uncommitted
    assert gate.run("t", lambda: _results(0.9), path, _lower, []) == 1
    assert gate.run("t", lambda: _results(0.8), path, _lower, []) == 0
    assert gate.run("t", lambda: _results(0.9), path, _lower, ["--write"]) == 0


def test_a_version_bump_alone_cannot_reset_the_bar() -> None:
    # verifier #171: bumping dataset_version without changing the dataset, or
    # moving it backwards, must not let --write lower a committed metric
    old = _results(0.8, version=2)
    assert len(gate.lowered(_results(0.1, version=3), old, _lower)) == 1
    assert gate.lowered(_results(0.1, sha="b", version=1), old, _lower) != []


def test_a_results_file_edited_to_claim_better_numbers_fails_ci(
    tmp_path: Path,
) -> None:
    import detectors

    path = tmp_path / "detectors.json"
    claimed = json.loads(detectors.RESULTS.read_text())
    # a metric below 1.0 (recall became 1.0 with #151)
    claimed["models"]["candidate"]["metrics"]["overall.precision"] = 1.0
    path.write_text(json.dumps(claimed))
    run = gate.run("detectors", detectors.evaluate, path, detectors.lower_is_better, [])
    assert run == 1


def test_a_float_model_reproduces_within_its_declared_tolerance(
    tmp_path: Path,
) -> None:
    # #149: ETS fits in float, so another platform's optimiser can move a
    # number in its last digits; such a harness declares how far
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_results(0.8)))
    near, far = _results(0.8 + 0.004), _results(0.8 + 0.006)
    assert gate.run("t", lambda: near, path, _lower, []) == 1  # exact by default
    assert gate.run("t", lambda: near, path, _lower, [], tolerance=0.005) == 0
    assert gate.run("t", lambda: far, path, _lower, [], tolerance=0.005) == 1
    renamed = {**_results(0.8), "dataset_sha256": "b"}  # text is always exact
    assert gate.run("t", lambda: renamed, path, _lower, [], tolerance=0.005) == 1
    recount = {**_results(0.8), "dataset_version": 1.004}  # so are integers
    assert gate.run("t", lambda: recount, path, _lower, [], tolerance=0.005) == 1


def test_the_float_tolerance_never_lets_write_lower_a_score(tmp_path: Path) -> None:
    # verifier #179: the tolerance is for reproducing a run, not for the
    # refuse-to-lower rule; a MASE worse by 0.004 is still refused
    path = tmp_path / "r.json"
    path.write_text(json.dumps(_results(0.8, err=0.5)))
    worse = _results(0.8, err=0.504)
    argv = ["--write", "--tolerance=1", "--reproduce-tolerance", "1"]  # no CLI knob
    assert gate.run("t", lambda: worse, path, _lower, argv, tolerance=0.005) == 1
    assert json.loads(path.read_text()) == _results(0.8, err=0.5)  # untouched
    worse_recall = _results(0.796)
    assert gate.run("t", lambda: worse_recall, path, _lower, argv, 0.005) == 1


def test_only_the_forecast_harness_declares_a_tolerance() -> None:
    evals = Path(__file__).resolve().parents[1] / "evals"
    tolerant = sorted(
        p.name
        for p in evals.glob("*.py")
        if "gate.run(" in (src := p.read_text()) and "TOLERANCE" in src
    )
    assert tolerant == ["forecast.py"]
