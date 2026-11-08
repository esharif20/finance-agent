"""Change-point detection on income and essential spending (#256,
docs/AI_DESIGN.md "Learning about the user", ADR 0015).

A series is one of `SERIES`: monthly income, or monthly money out on rent,
bills or groceries. The caller gives its Transactions as (id, date, signed
pence); a month is the sum of its rows (credits for income, money out for an
essential), never below zero, and a month with no rows counts as zero.

The method is a two-sided CUSUM, chosen over Bayesian online change-point
detection and the 3-vs-12-month-mean baseline by `evals/changepoint.py`. Each
month is scored against the current level's robust reference (the median and
MAD of up to `REFERENCE` earlier months since the last change), the score is
clipped at `CLIP`, and an alarm needs the sum of scores less `DRIFT` to pass
`THRESHOLD`. Clipping is what stops a one-off spike: one month adds at most
CLIP - DRIFT, so a shift must persist for three months to be reported. The
change month is where the cumulative sum last left zero.

Gate (ADR 0015): nothing is reported until the series has `MIN_MONTHS`
months, and below that the result is `Insufficient`. A detected change does
not reset any baseline here; only a change the user confirms does (#270).

Floats are allowed in this module only for the scores; every level that
leaves it is integer pence. The result is invariant to row order.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Literal

from finance_agent.engine.baselines import Insufficient

type Series = Literal["income", "rent", "bills", "groceries"]
SERIES: tuple[Series, ...] = ("income", "rent", "bills", "groceries")

MIN_MONTHS = 9  # ADR 0015: change-point detection needs nine months
MIN_REFERENCE = 3  # months a level needs before months are scored against it
REFERENCE = 12  # trailing months in a level's reference
MAD_SCALE = 1.4826  # MAD to a normal standard deviation
FLOOR = 0.03  # a flat series (rent) still has a scale: 3% of its level
MIN_SCALE = 1_000  # and at least £10
# tuned on the development seed of evals/changepoint.py
CLIP = 2.5
DRIFT = 1.0
THRESHOLD = 4.0

type Row = tuple[int, date, int]  # Transaction id, posted on, signed pence


@dataclass(frozen=True)
class LifeChange:
    """A `life_change` Insight candidate: `series` moved from `before` to
    `after` (monthly medians, pence) from `month`, confirmed in `detected`."""

    series: Series
    month: date  # the first month at the new level
    detected: date  # the month the shift was confirmed
    before: int
    after: int
    evidence: tuple[int, ...]  # Transaction ids from `month` to `detected`
    type: Literal["life_change"] = "life_change"


def _index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _date(i: int) -> date:
    return date(i // 12, i % 12 + 1, 1)


def _level(values: Sequence[int]) -> int:
    return round(median(values))


def scale(reference: Sequence[int]) -> tuple[float, float]:
    """The reference's robust (centre, scale) in pence."""
    m = median(reference)
    mad = median(abs(v - m) for v in reference)
    return m, max(MAD_SCALE * mad, FLOOR * abs(m), MIN_SCALE)


def alarms(values: Sequence[int]) -> list[tuple[int, int]]:
    """(change month, detected month) indices into `values`, oldest first."""
    out: list[tuple[int, int]] = []
    start = 0
    up = down = 0.0
    up_from = down_from = 0
    for t, x in enumerate(values):
        reference = values[max(start, t - REFERENCE) : t]
        if len(reference) < MIN_REFERENCE:
            up_from = down_from = t + 1
            continue
        m, s = scale(reference)
        z = max(-CLIP, min(CLIP, (x - m) / s))
        up = max(0.0, up + z - DRIFT)
        down = max(0.0, down - z - DRIFT)
        if up == 0.0:
            up_from = t + 1
        if down == 0.0:
            down_from = t + 1
        if t >= MIN_MONTHS - 1 and max(up, down) > THRESHOLD:
            start = up_from if up >= down else down_from
            out.append((start, t))
            up = down = 0.0
            up_from = down_from = t + 1
    return out


def monthly(
    rows: Iterable[Row], series: Series, through: date | None = None
) -> tuple[int, list[int], dict[int, list[int]]]:
    """(first month index, monthly values, Transaction ids by month index)."""
    sign = 1 if series == "income" else -1
    last = None if through is None else _index(through)
    sums: dict[int, int] = {}
    ids: dict[int, list[int]] = {}
    for tid, posted_on, pence in rows:
        i = _index(posted_on)
        if last is not None and i > last:
            continue
        sums[i] = sums.get(i, 0) + sign * pence
        ids.setdefault(i, []).append(tid)
    if not sums:
        return 0, [], {}
    first = min(sums)
    end = max(sums) if last is None else last
    return first, [max(0, sums.get(i, 0)) for i in range(first, end + 1)], ids


def detect_changes(
    rows: Iterable[Row], series: Series, through: date | None = None
) -> tuple[LifeChange, ...] | Insufficient:
    """The level shifts in one series, up to the month of `through` (default:
    its last row's month)."""
    first, values, ids = monthly(rows, series, through)
    if len(values) < MIN_MONTHS:
        return Insufficient(len(values), MIN_MONTHS)
    out: list[LifeChange] = []
    start = 0
    for change, detected in alarms(values):
        out.append(
            LifeChange(
                series,
                _date(first + change),
                _date(first + detected),
                _level(values[max(start, change - REFERENCE) : change]),
                _level(values[change : detected + 1]),
                tuple(
                    sorted(
                        tid
                        for i in range(first + change, first + detected + 1)
                        for tid in ids.get(i, ())
                    )
                ),
            )
        )
        start = change
    return tuple(out)
