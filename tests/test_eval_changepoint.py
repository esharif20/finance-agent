"""Change-point evaluation (#256): the dataset plants what it says, the scorer
counts delays and false alarms, and the committed results reproduce."""

import json

import changepoint
import gate
import synthetic_life_changes as lc


def test_dataset_is_seeded_and_plants_every_type() -> None:
    histories = lc.generate()
    assert lc.fingerprint(histories) == lc.fingerprint(lc.generate())
    types = {e.type for h in histories for e in h.shifts}
    assert types == {*lc.SHIFT_TYPES, "spike"}
    for h in histories:
        for e in h.shifts:
            assert e.month >= lc.FIRST_SHIFT
            if e.type != "spike":
                assert e.series == lc.SERIES_OF[e.type]
            others = [o.month for o in h.shifts if o.series == e.series and o != e]
            assert all(abs(e.month - m) >= lc.GAP for m in others)


def test_scorer_on_hand_detectors() -> None:
    histories = lc.generate(histories=4)
    silent = changepoint.score(histories, lambda _: [])
    assert silent["overall.recall"] == 0.0
    assert silent["false_alarms_per_series_year"] == 0.0
    assert silent["pay_rise.delay"] == changepoint.MAX_DELAY + 1
    # reporting every month finds every shift at once, with many false alarms
    every = changepoint.GATE
    noisy = changepoint.score(histories, lambda v: list(range(every, len(v))))
    assert noisy["spike_alarm_rate"] == 1.0
    assert noisy["false_alarms_per_series_year"] > 5


def test_baselines_never_report_before_the_gate() -> None:
    values = [100] * 4 + [300] * 20
    for detector in (changepoint.baseline, changepoint.bocpd):
        assert all(t >= changepoint.GATE for t in detector(values))


def test_committed_results_are_reproducible_and_the_candidate_wins() -> None:
    assert (
        gate.run(
            "changepoint",
            changepoint.evaluate,
            changepoint.RESULTS,
            changepoint.lower_is_better,
            [],
        )
        == 0
    )
    committed = json.loads(changepoint.RESULTS.read_text())
    assert committed["seed"] == lc.SEED
    models = committed["models"]
    ours, base = models["candidate"]["metrics"], models["baseline"]["metrics"]
    assert ours["overall.recall"] >= max(
        m["metrics"]["overall.recall"] for m in models.values()
    )
    assert ours["spike_alarm_rate"] <= base["spike_alarm_rate"]
    assert ours["spike_alarm_rate"] <= ours["false_alarms_per_series_year"]
    assert ours["overall.delay"] <= changepoint.MAX_DELAY
