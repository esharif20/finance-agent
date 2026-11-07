"""Classification metrics for evaluations (docs/ML_EVALUATIONS.md), in the
standard library only.

A prediction of None is an abstention (Uncategorised, sent to the Review
Queue): a miss for its gold Category, never a Category of its own. Macro F1
averages over the Categories in the gold labels only, so a Category with no
gold rows is reported by `per_category` but cannot raise or lower the mean.
"""

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
