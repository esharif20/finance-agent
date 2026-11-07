"""Recurring detection (#104; reused by M5's detectors, #148). Pure: no
database, no logging, integers only.

`detect_recurring` finds one regular series in a Merchant's charges. A series
needs at least `MIN_CHARGES` charges (the ADR 0015 gate), every gap between
consecutive charges inside one cadence's window, and every amount within ±5%
of the median charge. Anything else is not flagged: skipped periods, price
rises and drifting amounts are misses by design (precision first; the
evaluation in `evals/recurring.py` measures the cost in recall).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

MIN_CHARGES = 3
TOLERANCE_PCT = 5

# cadence -> inclusive window of days between charges, wide enough for
# calendar months (28-31 days) plus a few days' posting jitter either side
CADENCES: dict[str, tuple[int, int]] = {
    "weekly": (5, 9),
    "monthly": (24, 36),
    "annual": (350, 380),
}


@dataclass(frozen=True)
class Recurrence:
    interval: str  # a key of CADENCES: weekly, monthly or annual
    charge_count: int
    first_on: date
    last_on: date
    typical_minor: int  # the median charge, in minor units


def detect_recurring(
    charges: Iterable[tuple[date, int]], min_charges: int = MIN_CHARGES
) -> Recurrence | None:
    """The regular series in `charges`, or None.

    `charges` are (posted date, charge in minor units, positive for money
    out). Non-positive rows (refunds, reversals) are ignored. The result
    depends only on the multiset of charges, never on their order."""
    rows = sorted((d, a) for d, a in charges if a > 0)
    if len(rows) < max(min_charges, 2):
        return None
    gaps = [(b[0] - a[0]).days for a, b in zip(rows, rows[1:], strict=False)]
    interval = next(
        (c for c, (lo, hi) in CADENCES.items() if all(lo <= g <= hi for g in gaps)),
        None,
    )
    amounts = sorted(a for _, a in rows)
    median = amounts[len(amounts) // 2]  # the upper median: an integer, deterministic
    stable = all(abs(a - median) * 100 <= median * TOLERANCE_PCT for a in amounts)
    if interval is None or not stable:
        return None
    return Recurrence(interval, len(rows), rows[0][0], rows[-1][0], median)
