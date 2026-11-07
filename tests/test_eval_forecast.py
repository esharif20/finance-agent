"""Forecast evaluation (#146): MASE and prediction-interval coverage by
rolling-origin backtest, with naive and seasonal-naive baselines the ETS ticket
(#149) must beat."""

import json
from collections.abc import Sequence

import pytest

import forecast
import gate
import synthetic_history as sh
from forecast import Forecast

SEASONAL = [100 + 10 * (m % 12) + (50 if m % 12 == 11 else 0) for m in range(26)]


def test_naive_baselines_on_a_hand_case() -> None:
    y = [10, 12, 9, 13] + [20] * 9 + [14]  # 14 months
    assert forecast.naive(y, 2)[0].point == 14
    # seasonal naive repeats the month a year before
    # months 14 and 15 repeat months 2 and 3
    assert [f.point for f in forecast.seasonal_naive(y, 2)] == [9, 13]
    # the 80% interval is the 80th percentile of the matching in-sample errors
    lag1 = sorted(abs(a - b) for a, b in zip(y[1:], y, strict=False))
    q = lag1[-3]  # nearest rank: ceil(0.8 * 13) = 11th of 13
    f = forecast.naive(y, 1)[0]
    assert (f.lower, f.upper) == (14 - q, 14 + q)


def test_backtest_of_a_perfect_seasonal_forecaster() -> None:
    result = forecast.backtest(SEASONAL, forecast.seasonal_naive)
    assert result.mase == 0.0 and result.coverage == 1.0
    assert forecast.backtest(SEASONAL, forecast.naive).mase > 0.5


def test_backtest_uses_only_the_past_and_starts_at_the_adr_gate() -> None:
    lengths: list[int] = []

    def spy(history: Sequence[int], horizon: int) -> list[Forecast]:
        lengths.append(len(history))
        assert list(history) == SEASONAL[: len(history)]
        return forecast.naive(history, horizon)

    forecast.backtest(SEASONAL, spy)
    assert forecast.MIN_TRAIN == 13  # ADR 0015: ETS needs 13 months
    assert lengths == list(range(13, len(SEASONAL)))


def test_a_forecaster_must_return_well_formed_intervals() -> None:
    def bad(history: Sequence[int], horizon: int) -> list[Forecast]:
        return [Forecast(5, 6, 7)] * horizon  # point outside its interval

    with pytest.raises(ValueError):
        forecast.backtest(SEASONAL, bad)
    with pytest.raises(ValueError):
        forecast.backtest(SEASONAL, lambda h, n: forecast.naive(h, n)[:-1])


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "forecast",
            forecast.evaluate,
            forecast.RESULTS,
            forecast.lower_is_better,
            [],
        )
        == 0
    )
    committed = json.loads(forecast.RESULTS.read_text())
    metrics = committed["models"]["seasonal_naive"]["metrics"]
    for cat in sh.DISCRETIONARY:  # reported per Category, not only overall
        assert f"{sh.label(cat)}.mase" in metrics
    assert forecast.lower_is_better("overall.mase")
    assert forecast.lower_is_better("overall.coverage_gap")
