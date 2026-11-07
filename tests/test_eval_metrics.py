"""Evaluation metrics (docs/ML_EVALUATIONS.md): macro F1, per-Category
precision and recall, expected calibration error. Standard library only."""

import pytest

from metrics import expected_calibration_error, macro_f1, per_category

A, B, C = ("X", "a"), ("X", "b"), ("Y", "c")


def test_perfect_predictions_score_one() -> None:
    gold = [A, B, C, A]
    assert macro_f1(gold, gold) == 1.0
    assert per_category(gold, gold) == {
        A: (1.0, 1.0),
        B: (1.0, 1.0),
        C: (1.0, 1.0),
    }


def test_all_wrong_scores_zero() -> None:
    assert macro_f1([A, B], [B, A]) == 0.0


def test_worked_example() -> None:
    # A: tp 1, fp 1, fn 1 -> p 0.5 r 0.5; B the same; C never predicted -> 0, 0
    gold = [A, A, B, B, C]
    pred = [A, B, B, A, None]
    assert per_category(gold, pred) == {A: (0.5, 0.5), B: (0.5, 0.5), C: (0.0, 0.0)}
    # A: tp 1 of 1 predicted, 2 gold -> f1 2/3; B: tp 2 of 3 predicted, 2 gold
    # -> p 2/3 r 1 f1 0.8
    assert macro_f1([A, A, B, B], [A, B, B, B]) == pytest.approx((2 / 3 + 0.8) / 2)


def test_abstention_is_a_miss_and_not_a_class() -> None:
    # None (Uncategorised) lowers recall but is not averaged as a Category
    assert per_category([A, A], [A, None]) == {A: (1.0, 0.5)}
    assert macro_f1([A, A], [A, None]) == pytest.approx(2 / 3)


def test_class_with_no_gold_rows_is_reported_but_not_averaged() -> None:
    # C has no gold rows (an empty class): precision 0, recall 0, outside macro F1
    assert per_category([A, A], [A, C]) == {A: (1.0, 0.5), C: (0.0, 0.0)}
    assert macro_f1([A, A], [A, C]) == pytest.approx(2 / 3)


def test_empty_input() -> None:
    assert macro_f1([], []) == 0.0
    assert per_category([], []) == {}
    assert expected_calibration_error([], []) == 0.0


def test_lengths_must_match() -> None:
    with pytest.raises(ValueError):
        macro_f1([A], [])
    with pytest.raises(ValueError):
        per_category([A], [])
    with pytest.raises(ValueError):
        expected_calibration_error([1.0], [])


def test_calibration_perfect_and_worst() -> None:
    assert expected_calibration_error([1.0, 1.0, 0.0], [True, True, False]) == 0.0
    assert expected_calibration_error([1.0, 1.0], [False, False]) == 1.0
    assert expected_calibration_error([0.0, 0.0], [True, True]) == 1.0


def test_calibration_worked_example() -> None:
    # bin [0.7, 0.8): mean confidence 0.75, accuracy 0.5 -> gap 0.25, weight 1/2
    # last bin [0.9, 1.0]: confidence 1.0, accuracy 1.0 -> gap 0
    conf = [0.7, 0.79, 1.0, 1.0]
    ok = [True, False, True, True]
    assert expected_calibration_error(conf, ok) == pytest.approx(0.1225)


def test_confidence_outside_unit_interval_is_rejected() -> None:
    with pytest.raises(ValueError):
        expected_calibration_error([1.5], [True])
    with pytest.raises(ValueError):
        expected_calibration_error([-0.1], [True])
