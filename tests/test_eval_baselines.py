"""Personal baseline evaluation (#147): the committed results reproduce, and
the personal baselines beat the trailing mean they replace."""

import json

import baselines
import gate
import synthetic_history as sh


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "baselines",
            baselines.evaluate,
            baselines.RESULTS,
            baselines.lower_is_better,
            [],
        )
        == 0
    )


def test_personal_baselines_beat_the_trailing_mean() -> None:
    models = json.loads(baselines.RESULTS.read_text())["models"]
    ours, theirs = models["candidate"]["metrics"], models["baseline"]["metrics"]
    for name in (
        "seasonal.all.wape",
        "seasonal.year1.wape",
        "seasonal.after_year1.wape",
        "payday_week1.all.wape",
    ):
        assert ours[name] < theirs[name], name


def test_paydays_are_the_salary_days() -> None:
    h = sh.generate(sh.SEED, histories=1, months=sh.MIN_MONTHS)[0]
    days = baselines.paydays(h)
    assert days == [t.date for t in h.txns if t.merchant == "ACME LTD SALARY"]
