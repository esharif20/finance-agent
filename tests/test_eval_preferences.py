"""Pairwise preference evaluation (#255): the committed results reproduce, and
the engine beats both baselines at 10 answers."""

import json
import math

import pytest

import gate
import preferences
import synthetic_preferences as sp


def test_kendall_tau_hand_cases() -> None:
    assert preferences.kendall_tau([1, 2, 3], [10, 20, 30]) == 1.0
    assert preferences.kendall_tau([1, 2, 3], [30, 20, 10]) == -1.0
    assert preferences.kendall_tau([0, 0, 0], [1, 2, 3]) == 0.0
    # tau-b with one tie in x: (2 - 0) / sqrt(2 * 3)
    assert preferences.kendall_tau([1, 1, 2], [1, 2, 3]) == pytest.approx(
        2 / math.sqrt(6)
    )


def test_log_loss_and_ece_hand_cases() -> None:
    assert preferences.log_loss([(0.5, True), (0.5, False)]) == pytest.approx(
        math.log(2)
    )
    assert preferences.ece([(0.5, True), (0.5, False)]) == 0.0
    assert preferences.ece([(0.9, False), (0.9, False)]) == pytest.approx(0.9)


def test_the_dataset_is_seeded_and_in_range() -> None:
    users = sp.users()
    assert sp.fingerprint(users) == sp.fingerprint(sp.users())
    assert all(12 <= len(u.items) <= 20 for u in users)
    assert all(len(u.answers) == len(u.items) * (len(u.items) - 1) // 2 for u in users)


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "preferences",
            preferences.evaluate,
            preferences.RESULTS,
            preferences.lower_is_better,
            [],
            preferences.REPRODUCE_TOLERANCE,
        )
        == 0
    )


def test_the_engine_beats_both_baselines_at_ten_answers() -> None:
    models = json.loads(preferences.RESULTS.read_text())["models"]
    active = models["bt_active"]["metrics"]
    for baseline in ("bt_random", "spend_share"):
        other = models[baseline]["metrics"]
        assert active["tau@10"] > other["tau@10"], baseline
        assert active["log_loss@10"] < other["log_loss@10"], baseline
    assert preferences.lower_is_better("log_loss@10")
    assert preferences.lower_is_better("ece@10")
    assert not preferences.lower_is_better("tau@10")
