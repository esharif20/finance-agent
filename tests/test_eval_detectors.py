"""Detector evaluation (#146): precision and recall per planted event type,
the trivial-rule baseline and the stub the detector tickets (#147, #148)
replace."""

import json
from datetime import date

import detectors
import gate
import synthetic_history as sh
from detectors import Detection, Row

E = sh.Event
D = date(2024, 3, 10)


def _event(t: str, d: date = D, key: str = "STREAMLY") -> sh.Event:
    return E(t, d, 0, key, sh.NONE, 100, ())


def test_a_detection_matches_one_event_of_its_type_and_key_inside_the_window() -> None:
    events = [_event("price_rise")]
    hit = Detection("price_rise", date(2024, 3, 20), "STREAMLY")
    assert detectors.match(events, [hit]) == {"price_rise": (1, 0, 0)}
    # a second detection of the same event is a false positive
    assert detectors.match(events, [hit, hit]) == {"price_rise": (1, 1, 0)}
    # wrong key, wrong type, too early: misses and false positives
    wrong = [
        Detection("price_rise", D, "PIXELFORGE SOFTWARE"),
        Detection("category_spike", D, "STREAMLY"),
        Detection("price_rise", date(2024, 3, 9), "STREAMLY"),
    ]
    assert detectors.match(events, wrong) == {
        "price_rise": (0, 2, 1),
        "category_spike": (0, 1, 0),
    }


def test_shortfall_warnings_must_come_before_the_balance_goes_negative() -> None:
    events = [_event("payday_shortfall", key="")]
    early = Detection("payday_shortfall", date(2024, 3, 4), "")
    same_day = Detection("payday_shortfall", D, "")
    assert detectors.match(events, [early]) == {"payday_shortfall": (1, 0, 0)}
    assert detectors.match(events, [same_day]) == {"payday_shortfall": (0, 1, 1)}


def _row(i: int, d: date, merchant: str, amount: int, balance: int = 10_000) -> Row:
    return Row(i, d, merchant, ("Subscriptions", "Streaming"), amount, balance)


def test_baseline_rules_on_hand_cases() -> None:
    rows = [
        _row(1, date(2024, 1, 5), "STREAMLY", -1099),
        _row(2, date(2024, 2, 5), "STREAMLY", -1099),
        _row(3, date(2024, 3, 5), "STREAMLY", -1199),  # a rise
        _row(4, date(2024, 1, 9), "CLOUDNOTE ANNUAL", -4999),  # seen once
    ]
    found = set(detectors.baseline(rows))
    assert Detection("price_rise", date(2024, 3, 5), "STREAMLY") in found
    # a once-a-year Merchant is predicted to renew 358 days on
    assert Detection("forgotten_renewal", date(2025, 1, 1), "CLOUDNOTE ANNUAL") in found
    # too little history for a baseline or a series: the candidate flags only
    # the Subscriptions charge seen once, four weeks before it renews
    assert detectors.candidate(rows) == [
        Detection(
            "forgotten_renewal", date(2024, 12, 12), "CLOUDNOTE ANNUAL", 4999, (4,)
        )
    ]


def test_a_hit_is_scored_on_its_evidence_and_impact() -> None:
    rise = E("price_rise", D, 0, "STREAMLY", sh.NONE, 1_200, (7, 8))
    good = Detection("price_rise", D, "STREAMLY", 1_200, (7, 8))
    assert detectors.evidence_valid(rise, good)
    assert detectors.impact_consistent(rise, good)
    # a row outside the event, no evidence at all, no or a wrong impact
    assert not detectors.evidence_valid(rise, good._replace(evidence=(7, 9)))
    assert not detectors.evidence_valid(rise, good._replace(evidence=()))
    assert not detectors.impact_consistent(rise, good._replace(impact=None))
    assert not detectors.impact_consistent(rise, good._replace(impact=1_300))
    spike = E("category_spike", D, 0, "Food / Takeaway", sh.NONE, 1_000, (1,))
    hit = Detection("category_spike", D, "Food / Takeaway", 400, (1,))
    assert detectors.impact_consistent(spike, hit)  # the excess so far
    assert not detectors.impact_consistent(spike, hit._replace(impact=0))
    assert not detectors.impact_consistent(spike, hit._replace(impact=1_501))


def test_the_engine_beats_the_trivial_rules_and_says_true_things() -> None:
    models = json.loads(detectors.RESULTS.read_text())["models"]
    got, base = models["candidate"]["metrics"], models["baseline"]["metrics"]
    for t in ("price_rise", "category_spike", "forgotten_renewal"):
        assert got[f"{t}.precision"] > base[f"{t}.precision"]
        assert got[f"{t}.recall"] >= base[f"{t}.recall"]
        assert got[f"{t}.evidence_valid"] == 1.0
        assert got[f"{t}.impact_consistent"] == 1.0


def test_the_candidate_flags_a_spike_against_the_personal_baseline() -> None:
    cat = ("Food & Drink", "Takeaway")
    rows = [
        Row(m, date(2024, m, 10), "WOKWAY KITCHEN", cat, -1_000, 0) for m in range(1, 8)
    ]
    rows += [  # July: 1,000, then 600 more crosses 1.5 x the 1,000 normal
        Row(8, date(2024, 7, 20), "WOKWAY KITCHEN", cat, -600, 0),
        Row(9, date(2024, 7, 25), "WOKWAY KITCHEN", cat, -600, 0),
    ]
    found = detectors.candidate(rows)
    # 1,600 spent against 1,000 expected, on the July rows so far
    assert found == [
        Detection("category_spike", date(2024, 7, 20), sh.label(cat), 600, (7, 8))
    ]
    # the same July a month short of the six-month gate is not flagged
    assert detectors.candidate([r for r in rows if r.id != 1]) == []


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "detectors",
            detectors.evaluate,
            detectors.RESULTS,
            detectors.lower_is_better,
            [],
        )
        == 0
    )
    committed = json.loads(detectors.RESULTS.read_text())
    assert committed["seed"] == sh.SEED
    assert committed["models"]["baseline"]["model_version"] is not None
    for t in sh.EVENT_TYPES:  # reported per type, not only overall
        assert f"{t}.recall" in committed["models"]["baseline"]["metrics"]
    # every planted price rise is a monthly charge going up: the rule finds all
    assert committed["models"]["baseline"]["metrics"]["price_rise.recall"] == 1.0


def test_baseline_survives_empty_and_single_row_histories() -> None:
    assert detectors.baseline([]) == []
    one = [_row(1, D, "STREAMLY", -1099)]
    assert all(d.type in sh.EVENT_TYPES for d in detectors.baseline(one))


def test_trivial_rules_cannot_saturate_spike_recall() -> None:
    # verifier #171: post-peak spikes sit under a trailing-mean threshold, so a
    # detector must know the seasonal normal to find them all
    spikes = json.loads(detectors.RESULTS.read_text())["models"]["baseline"]
    assert spikes["metrics"]["category_spike.recall"] < 1.0
