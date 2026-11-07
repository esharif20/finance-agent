"""Recurring detection (#104; reused by M5's detectors, #148). Pure: no
database, no logging, integers only. It lives in `categorise`, which
recognition needs, because `engine` depends on `categorise` and not the
other way round (docs/ARCHITECTURE.md).

`detect_recurring` judges the latest run of a Merchant's charges (#177), so
an old skipped month, price rise or one-off no longer hides a current series.
A history whose every gap fits one cadence and every amount is within ±5% of
its median is one run. Otherwise the run grows back from the latest charge
while each earlier charge falls one cadence window before the next and within
±5% of the run's median so far, and the run stays within ±5% of its own
median; the first charge that fails ends it. A run is flagged when it has
at least `MIN_CHARGES` charges (the ADR 0015 gate). A price step therefore
starts a new run; judging the step itself is the price-rise detector's job
(#148). Drifting amounts break the run every
couple of charges and are not flagged (precision first; `evals/recurring.py`
measures the cost in recall).
"""

from bisect import insort
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
    charge_count: int  # charges in the latest run
    first_on: date  # the run's first charge
    last_on: date
    typical_minor: int  # the run's median charge, in minor units


def detect_recurring(
    charges: Iterable[tuple[date, int]], min_charges: int = MIN_CHARGES
) -> Recurrence | None:
    """The regular series that the latest charges form, or None.

    `charges` are (posted date, charge in minor units, positive for money
    out). Non-positive rows (refunds, reversals) are ignored. The result
    depends only on the multiset of charges, never on their order."""
    rows = sorted((d, a) for d, a in charges if a > 0)
    if len(rows) < max(min_charges, 2):
        return None
    last_gap = (rows[-1][0] - rows[-2][0]).days
    interval = next(
        (c for c, (lo, hi) in CADENCES.items() if lo <= last_gap <= hi), None
    )
    if interval is None:
        return None
    lo, hi = CADENCES[interval]
    whole = sorted(a for _, a in rows)
    gaps = ((b[0] - a[0]).days for a, b in zip(rows, rows[1:], strict=False))
    if all(lo <= g <= hi for g in gaps) and all(
        _within(a, _median(whole)) for a in whole
    ):
        # a history regular as a whole is one run: the step rule below is
        # stricter on jitter than one band around the whole history's median
        return Recurrence(interval, len(rows), rows[0][0], rows[-1][0], _median(whole))
    amounts = [rows[-1][1]]  # the run's amounts, kept sorted
    first = len(rows) - 1
    while first > 0:
        (on, amount), later = rows[first - 1], rows[first][0]
        if not lo <= (later - on).days <= hi or not _within(amount, _median(amounts)):
            break
        trial = sorted([*amounts, amount])
        if not all(_within(a, _median(trial)) for a in trial):
            break
        insort(amounts, amount)
        first -= 1
    if len(amounts) < min_charges:
        return None
    return Recurrence(
        interval, len(amounts), rows[first][0], rows[-1][0], _median(amounts)
    )


def _median(sorted_amounts: list[int]) -> int:
    return sorted_amounts[
        len(sorted_amounts) // 2
    ]  # upper median: integer, deterministic


def _within(amount: int, median: int) -> bool:
    return abs(amount - median) * 100 <= median * TOLERANCE_PCT
