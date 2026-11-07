"""Forecast evaluation (#146, docs/ML_EVALUATIONS.md): MASE and 80%
prediction-interval coverage of monthly discretionary spend per Category, by
rolling-origin backtest, against naive and seasonal-naive baselines.

    uv run python evals/forecast.py            # CI: results reproduce
    uv run python evals/forecast.py --write    # refresh (see gate.py)

The ETS ticket (#149) implements `Forecaster` and replaces `stub` in MODELS.
A forecaster gets the monthly totals so far (pence) and returns `horizon`
forecasts with an interval at `LEVEL`. Origins run from `MIN_TRAIN` months
(ADR 0015's ETS gate) to the end, up to `HORIZON` months ahead. MASE scales
each origin's errors by its training window's one-step naive error.
`coverage_gap` is |coverage - LEVEL|, so too-wide intervals score no better
than too-narrow ones.
"""

import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import gate
import synthetic_history as sh
from metrics import interval_coverage, mase, naive_scale

RESULTS = Path(__file__).parent / "results" / "forecast_baseline.json"
LEVEL = 0.8
HORIZON = 3
MIN_TRAIN = 13
SEASON = 12


class Forecast(NamedTuple):
    point: int  # pence
    lower: int
    upper: int


class Forecaster(Protocol):
    def __call__(
        self, history: Sequence[int], horizon: int, /
    ) -> Sequence[Forecast]: ...


class Backtest(NamedTuple):
    mase: float
    coverage: float


def _half_width(history: Sequence[int], lag: int) -> int:
    """Nearest-rank LEVEL quantile of the in-sample |y[t] - y[t-lag]|."""
    errors = sorted(abs(a - b) for a, b in zip(history[lag:], history, strict=False))
    return errors[math.ceil(LEVEL * len(errors)) - 1]


def _band(point: int, q: int) -> Forecast:
    return Forecast(point, max(0, point - q), point + q)  # spend is never negative


def naive(history: Sequence[int], horizon: int) -> list[Forecast]:
    """The last month, repeated."""
    return [_band(history[-1], _half_width(history, h)) for h in range(1, horizon + 1)]


def seasonal_naive(history: Sequence[int], horizon: int) -> list[Forecast]:
    """The same month a year before (horizon <= 12)."""
    q = _half_width(history, SEASON)
    n = len(history)
    return [_band(history[n + h - 1 - SEASON], q) for h in range(1, horizon + 1)]


def stub(history: Sequence[int], horizon: int) -> list[Forecast]:
    """Placeholder until #149: the training mean, with no interval."""
    mean = sum(history) // len(history)
    return [Forecast(mean, mean, mean)] * horizon


MODELS: dict[str, tuple[str, str, Forecaster]] = {
    "naive": ("naive", "1", naive),
    "seasonal_naive": ("seasonal-naive", "1", seasonal_naive),
    "candidate": ("stub", "0", stub),
}


def backtest(series: Sequence[int], model: Forecaster) -> Backtest:
    scaled: list[float] = []
    actual: list[int] = []
    bands: list[tuple[int, int]] = []
    for t in range(MIN_TRAIN, len(series)):
        train, test = series[:t], series[t : t + HORIZON]
        fs = list(model(list(train), len(test)))
        if len(fs) != len(test) or any(not f.lower <= f.point <= f.upper for f in fs):
            raise ValueError(
                "a forecaster must return `horizon` intervals around points"
            )
        scaled.append(mase(test, [f.point for f in fs], naive_scale(train)))
        actual += test
        bands += [(f.lower, f.upper) for f in fs]
    return Backtest(sum(scaled) / len(scaled), interval_coverage(actual, bands))


def evaluate() -> dict[str, Any]:
    histories = sh.generate(sh.SEED)
    models: dict[str, Any] = {}
    for key, (name, version, model) in MODELS.items():
        metrics: dict[str, float] = {}
        coverage: dict[str, float] = {}
        everything: list[Backtest] = []
        for cat in sorted(sh.DISCRETIONARY):
            runs = [
                backtest([sh.month_spend(h, cat, m) for m in range(h.months)], model)
                for h in histories
            ]
            everything += runs
            cov = sum(r.coverage for r in runs) / len(runs)
            metrics[f"{sh.label(cat)}.mase"] = round(
                sum(r.mase for r in runs) / len(runs), 6
            )
            metrics[f"{sh.label(cat)}.coverage_gap"] = round(abs(cov - LEVEL), 6)
            coverage[sh.label(cat)] = round(cov, 6)
        cov = sum(r.coverage for r in everything) / len(everything)
        metrics["overall.mase"] = round(
            sum(r.mase for r in everything) / len(everything), 6
        )
        metrics["overall.coverage_gap"] = round(abs(cov - LEVEL), 6)
        coverage["overall"] = round(cov, 6)
        models[key] = {
            "model": name,
            "model_version": version,
            "metrics": metrics,
            "coverage": coverage,
        }
    return {
        "harness": "forecast",
        "dataset": "synthetic_history",
        "dataset_version": sh.DATASET_VERSION,
        "dataset_sha256": sh.fingerprint(histories),
        "seed": sh.SEED,
        "histories": len(histories),
        "months": sh.MONTHS,
        "level": LEVEL,
        "horizon": HORIZON,
        "min_train": MIN_TRAIN,
        "models": models,
    }


def lower_is_better(name: str) -> bool:
    return name.endswith((".mase", ".coverage_gap"))


if __name__ == "__main__":
    sys.exit(gate.run("forecast", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
