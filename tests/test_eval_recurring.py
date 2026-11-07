"""The recurring-detection evaluation (#104) is reproducible and gates CI."""

import recurring


def test_committed_results_match_a_fresh_run_and_beat_the_baseline() -> None:
    assert recurring.main([]) == 0
    assert recurring.evaluate()["detector"]["precision"] == 1.0  # no false flags


def test_a_worse_detector_or_a_changed_dataset_is_a_regression() -> None:
    fresh = recurring.evaluate()
    worse = {**fresh, "detector": {**fresh["detector"], "recall": 0.0}}
    assert recurring.regressions(worse, fresh) == [
        "detector recall fell",
        "detector recall below baseline",
    ]
    assert recurring.regressions({**fresh, "seed": 1}, fresh) == ["seed changed"]
