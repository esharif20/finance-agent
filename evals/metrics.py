"""Metrics for evaluations (docs/ML_EVALUATIONS.md), in the standard library
only: classification (#112), ranking and forecast (#146).

A prediction of None is an abstention (Uncategorised, sent to the Review
Queue): a miss for its gold Category, never a Category of its own. Macro F1
averages over the Categories in the gold labels only, so a Category with no
gold rows is reported by `per_category` but cannot raise or lower the mean.
"""

import math
from collections import Counter
from collections.abc import Hashable, Sequence


def _same_length(a: Sequence[object], b: Sequence[object]) -> None:
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} != {len(b)}")


def _counts[L: Hashable](
    gold: Sequence[L], pred: Sequence[L | None]
) -> tuple[Counter[L], Counter[L], Counter[L]]:
    """True positives, predicted and gold counts per label."""
    _same_length(gold, pred)
    tp: Counter[L] = Counter(g for g, p in zip(gold, pred, strict=True) if g == p)
    predicted: Counter[L] = Counter(p for p in pred if p is not None)
    return tp, predicted, Counter(gold)


def _ratio(a: int, b: int) -> float:
    return a / b if b else 0.0


def per_category[L: Hashable](
    gold: Sequence[L], pred: Sequence[L | None]
) -> dict[L, tuple[float, float]]:
    """(precision, recall) for every label that is gold or predicted."""
    tp, predicted, actual = _counts(gold, pred)
    return {
        label: (_ratio(tp[label], predicted[label]), _ratio(tp[label], actual[label]))
        for label in actual | predicted
    }


def macro_f1[L: Hashable](gold: Sequence[L], pred: Sequence[L | None]) -> float:
    """Unweighted mean F1 over the gold labels; 0.0 when there are none."""
    tp, predicted, actual = _counts(gold, pred)
    f1s = [_ratio(2 * tp[label], predicted[label] + actual[label]) for label in actual]
    return sum(f1s) / len(f1s) if f1s else 0.0


def expected_calibration_error(
    confidence: Sequence[float], correct: Sequence[bool], bins: int = 10
) -> float:
    """Sum over equal-width confidence bins of |accuracy - mean confidence|,
    weighted by the bin's share of rows. 1.0 falls in the last bin."""
    _same_length(confidence, correct)
    if any(not 0.0 <= c <= 1.0 for c in confidence):
        raise ValueError("confidence must be in [0, 1]")
    binned: dict[int, list[tuple[float, bool]]] = {}
    for c, ok in zip(confidence, correct, strict=True):
        binned.setdefault(min(int(c * bins), bins - 1), []).append((c, ok))
    total = len(confidence)
    return sum(
        abs(sum(ok for _, ok in rows) - sum(c for c, _ in rows)) / total
        for rows in binned.values()
    )


# --- ranking and forecast metrics (#146) ------------------------------------


def ndcg_at_k(gains: Sequence[int], k: int) -> float:
    """NDCG@k of gains listed in ranked order, with linear gains. 1.0 when no
    item has a gain, since every order is then ideal."""
    if k < 1:
        raise ValueError("k must be at least 1")
    if any(g < 0 for g in gains):
        raise ValueError("gains must be non-negative")

    def dcg(gs: Sequence[int]) -> float:
        return sum(g / math.log2(i + 2) for i, g in enumerate(gs[:k]))

    ideal = dcg(sorted(gains, reverse=True))
    return dcg(gains) / ideal if ideal else 1.0


def naive_scale(train: Sequence[int], m: int = 1) -> float:
    """The MASE scale: mean |y[t] - y[t-m]| over the training series."""
    diffs = [abs(a - b) for a, b in zip(train[m:], train, strict=False)]
    if not diffs or not any(diffs):
        raise ValueError("MASE is undefined: no non-zero lag-m differences")
    return sum(diffs) / len(diffs)


def mase(actual: Sequence[int], forecast: Sequence[int], scale: float) -> float:
    """Mean absolute error of `forecast`, divided by `scale` (`naive_scale`)."""
    _same_length(actual, forecast)
    if not actual or scale <= 0:
        raise ValueError("need forecasts and a positive scale")
    errors = [abs(a - f) for a, f in zip(actual, forecast, strict=True)]
    return sum(errors) / len(errors) / scale


def interval_coverage(
    actual: Sequence[int], intervals: Sequence[tuple[int, int]]
) -> float:
    """Share of actuals inside their (lower, upper) interval, bounds inclusive."""
    _same_length(actual, intervals)
    if not actual:
        raise ValueError("no forecasts to score")
    if any(lo > hi for lo, hi in intervals):
        raise ValueError("interval lower bound above its upper bound")
    inside = sum(lo <= a <= hi for a, (lo, hi) in zip(actual, intervals, strict=True))
    return inside / len(actual)
