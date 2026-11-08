"""Personal baselines per Category (#147, docs/AI_DESIGN.md, ADR 0015).

Pure functions over one Category's Transactions, given as (date, signed
pence) pairs as the Ledger holds them, spending negative. A month counts its
spending after Refunds, as the Ledger reports a Category, and never below
zero. Results are integer pence.

- `seasonal_baseline`: what a calendar month is expected to cost. The median
  month of the last six; once a year exists, the median of the same calendar
  month in earlier years instead, so December is compared with Decembers.
- `payday_baseline`: what is expected to be spent so far into a pay cycle:
  the median cycle's spend times the share spent by that day over complete
  cycles, so the week after payday is not compared with a month's average day.

After a life change the user confirmed (#270), `seasonal_baseline(since=)`
reads only the months from the change: the median of up to six of them, and
the same calendar month in earlier years only once those years are after it.
The ADR 0015 gate still counts the whole history, as the Category's history
is not lost, only out of date; a month with no complete month since the
change is `Insufficient(0, 1)`.

Each declares its ADR 0015 minimum and returns `Insufficient` below it, never
an estimate from too little data. Both are invariant to row order.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

MIN_MONTHS = 6  # ADR 0015: personal baselines need six months for the Category
MIN_CYCLES = 6  # the same six months, counted in complete pay cycles
TRAILING = 6  # months in the non-seasonal median
SEASONAL_YEARS = 3  # earlier same months the seasonal median looks back on

type Rows = Iterable[tuple[date, int]]


@dataclass(frozen=True)
class Insufficient:
    """Not enough history yet: `have` of the `need` months or cycles."""

    have: int
    need: int


@dataclass(frozen=True)
class SeasonalBaseline:
    expected: int  # pence of money out expected in the month
    months: int  # months of history it rests on
    seasonal: bool  # True once it follows the same month in earlier years


@dataclass(frozen=True)
class PaydayBaseline:
    by_day: tuple[int, ...]  # pence expected spent by day 0, 1, ... of a cycle
    cycle: int  # median pence spent in a whole cycle
    cycles: int  # complete cycles it rests on

    def spent_by(self, day: int) -> int:
        """Expected pence spent from payday through `day` days after it."""
        return self.by_day[day] if 0 <= day < len(self.by_day) else self.cycle


def _median(values: Sequence[int]) -> int:
    s = sorted(values)
    mid = len(s) // 2
    # half a penny rounds to even, as Decimal's default does
    return s[mid] if len(s) % 2 else _round(s[mid - 1] + s[mid], 2)


def _round(n: int, d: int) -> int:
    """n / d to the nearest integer, half to even (Decimal's default), d > 0."""
    q, r = divmod(n, d)
    return q + (2 * r > d or (2 * r == d and q % 2))


def _month(d: date) -> int:
    return d.year * 12 + d.month - 1


def seasonal_baseline(
    rows: Rows, month: date, since: date | None = None
) -> SeasonalBaseline | Insufficient:
    """The expected spend in the calendar month of `month`, from rows before it,
    and only from the month of `since` (a confirmed life change) when given."""
    target = _month(month)
    spend: dict[int, int] = {}
    for d, amount in rows:
        m = _month(d)
        if m < target:
            spend[m] = spend.get(m, 0) - amount
    spend = {m: max(0, v) for m, v in spend.items()}  # a month of net Refunds is 0
    have = target - min(spend) if spend else 0
    if have < MIN_MONTHS:
        return Insufficient(have, MIN_MONTHS)
    first = target - have if since is None else max(target - have, _month(since))
    if first >= target:
        return Insufficient(0, 1)
    same = [spend.get(target - 12 * y, 0) for y in range(1, SEASONAL_YEARS + 1)]
    same = same[: (target - first) // 12]
    if same:
        return SeasonalBaseline(_median(same), have, True)
    trailing = [spend.get(target - i, 0) for i in range(1, TRAILING + 1)]
    return SeasonalBaseline(_median(trailing[: target - first]), have, False)


def payday_baseline(
    rows: Rows, paydays: Iterable[date], as_of: date
) -> PaydayBaseline | Insufficient:
    """Spend by day into a pay cycle, over the cycles complete by `as_of`.

    A cycle runs from one payday up to the day before the next. Only cycles
    that end after the Category's first Transaction count: before it there is
    no history, not a history of spending nothing. Within a cycle only money
    out counts, so spend so far never falls."""
    rows = list(rows)
    days = sorted({p for p in paydays if p <= as_of})
    first = min((d for d, _ in rows), default=None)
    cycles = [
        (a, b)
        for a, b in zip(days, days[1:], strict=False)
        if first is not None and b > first
    ]
    if len(cycles) < MIN_CYCLES:
        return Insufficient(len(cycles), MIN_CYCLES)
    length = min((b - a).days for a, b in cycles)
    daily = [[0] * (b - a).days for a, b in cycles]
    for d, amount in rows:
        for (a, b), spent in zip(cycles, daily, strict=True):
            if a <= d < b:
                spent[(d - a).days] += max(0, -amount)
                break
    # the share spent by each day, pooled over cycles: a median of lumpy days
    # would often be 0 for a Category with a few charges a cycle
    total = sum(sum(c) for c in daily)
    by_day = [sum(sum(c[: i + 1]) for c in daily) for i in range(length)]
    cycle = _median([sum(c) for c in daily])
    return PaydayBaseline(
        tuple(_round(cycle * s, total) if total else 0 for s in by_day),
        cycle,
        len(cycles),
    )
