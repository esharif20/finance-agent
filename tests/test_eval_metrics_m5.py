"""Ranking and forecast metrics for the M5 harnesses (#146): NDCG@k, MASE and
prediction-interval coverage."""

import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from metrics import interval_coverage, mase, naive_scale, ndcg_at_k

gains = st.lists(st.integers(0, 10**9), min_size=1, max_size=30)


@given(gains, st.integers(1, 40), st.randoms(use_true_random=False))
def test_ndcg_is_in_unit_interval_and_one_for_the_oracle(
    g: list[int], k: int, rnd: random.Random
) -> None:
    shuffled = g[:]
    rnd.shuffle(shuffled)
    assert 0.0 <= ndcg_at_k(shuffled, k) <= 1.0 + 1e-12
    assert ndcg_at_k(sorted(g, reverse=True), k) == pytest.approx(1.0)


def test_ndcg_worked_example() -> None:
    # DCG@2 of [1, 3] = 1/log2(2) + 3/log2(3); ideal [3, 1] = 3 + 1/log2(3)
    expected = (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3))
    assert ndcg_at_k([1, 3, 0], 2) == pytest.approx(expected)
    assert ndcg_at_k([0, 0], 2) == 1.0  # nothing relevant: every order is ideal
    with pytest.raises(ValueError):
        ndcg_at_k([1, -1], 2)
    with pytest.raises(ValueError):
        ndcg_at_k([1], 0)


series = st.lists(st.integers(0, 10**7), min_size=3, max_size=40)


@given(series, st.integers(1, 12))
def test_naive_forecaster_scores_mase_one_on_its_own_scale(
    y: list[int], m: int
) -> None:
    if all(a == b for a, b in zip(y[m:], y, strict=False)):
        return  # MASE is undefined: no lag-m differences, or all zero
    # the in-sample lag-m naive forecast of y[t] is y[t - m]
    assert mase(y[m:], y[:-m], naive_scale(y, m)) == pytest.approx(1.0)


def test_mase_worked_example_and_undefined_scale() -> None:
    train = [10, 12, 9, 13]  # |diffs| 2, 3, 4 -> scale 3
    assert naive_scale(train) == 3
    assert mase([14, 10], [13, 13], 3) == pytest.approx((1 + 3) / 2 / 3)
    with pytest.raises(ValueError):
        naive_scale([5, 5, 5])  # zero scale: MASE undefined
    with pytest.raises(ValueError):
        naive_scale([5], 1)


def test_interval_coverage_hand_cases() -> None:
    # bounds are inclusive
    assert interval_coverage([5, 10, 15], [(5, 5), (0, 9), (15, 20)]) == 2 / 3
    assert interval_coverage([1, 2], [(0, 3), (2, 2)]) == 1.0
    assert interval_coverage([1, 2], [(2, 3), (3, 4)]) == 0.0
    with pytest.raises(ValueError):
        interval_coverage([1], [(3, 2)])  # lower above upper
    with pytest.raises(ValueError):
        interval_coverage([], [])
    with pytest.raises(ValueError):
        interval_coverage([1], [])
