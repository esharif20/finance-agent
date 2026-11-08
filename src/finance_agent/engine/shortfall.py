"""Cash-flow shortfall risk and what-if purchases (#151, docs/AI_DESIGN.md,
ADR 0015).

Pure functions in integer pence. The balance is projected day by day up to
the day before the next payday (`forecast.CashFlow`):

- scheduled payments and income on their projected dates, at their usual
  amount;
- discretionary spend (everything else) at the user's own pace through a pay
  cycle (`baselines.payday_baseline`, so the week after payday is not spread
  over the month): the median cycle for `minimum`, and for `floor`, the
  bottom of the balance's band, the larger of that and the top of the
  forecast band for the cycle's share of its month.

The risk and the shortfall Insight follow `minimum`. On the floor, precision
fell from 1.0 to 0.3 (`evals/detectors.py`): a one-off big expense comes
back through the Category's seasonal forecast a year on. Below ADR 0015's
six complete pay cycles, or with no payday projected, the answer is
`Insufficient`, never a guess.
"""

import calendar
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from finance_agent.categorise import normalise
from finance_agent.engine.baselines import (
    MIN_CYCLES,
    Insufficient,
    PaydayBaseline,
    payday_baseline,
)
from finance_agent.engine.detectors import Insight
from finance_agent.engine.forecast import CashFlow, Txn

type Risk = Literal["none", "below_buffer", "below_zero"]

CONF_SHORTFALL = 80  # a fixed prior until the ranker (#150) calibrates it


@dataclass(frozen=True)
class Pace:
    """Discretionary spend through a pay cycle, and the latest payday."""

    baseline: PaydayBaseline
    last_payday: date


@dataclass(frozen=True)
class Outlook:
    payday: date  # the first projected payday after the purchase
    minimum: int  # pence: the lowest projected balance before it
    on: date  # the first day it is reached
    risk: Risk  # of `minimum`
    # pence: the band's bottom, with discretionary spend at the top of the
    # forecast band where that is above the median cycle
    floor: int


def pace(txns: Iterable[Txn], flow: CashFlow, as_of: date) -> Pace | Insufficient:
    """The payday baseline of Spending outside `flow`'s scheduled payments,
    over the pay cycles of its recurring income complete by `as_of`."""
    payers = {r.merchant for r in flow.income}
    scheduled = {r.merchant for r in flow.recurring}
    paydays: set[date] = set()
    rows: list[tuple[date, int]] = []
    for t in txns:
        c = t.categorisation
        if c is None or t.posted_on > as_of:
            continue
        if c.kind == "Income" and normalise(t.description) in payers:
            paydays.add(t.posted_on)
        elif c.kind == "Spending" and (c.merchant or "") not in scheduled:
            rows.append((t.posted_on, int(t.amount * 100)))
    if not paydays:
        return Insufficient(0, MIN_CYCLES)
    b = payday_baseline(rows, paydays, as_of)
    return b if isinstance(b, Insufficient) else Pace(b, max(paydays))


def outlook(
    balance: int,
    as_of: date,
    flow: CashFlow,
    spend: Pace | Insufficient,
    purchase: int = 0,
    when: date | None = None,
    buffer: int = 0,
    paid_to: date | None = None,
) -> Outlook | Insufficient:
    """The lowest projected balance from `balance`, at the end of `as_of`, to
    the day before the first payday after `when` (default `as_of`), with
    `purchase` pence spent on `when`. `flow` is `cash_flow` from `as_of` and
    `spend` is `pace` then. Income projected on or before `paid_to` is taken
    as already in `balance` (a fresher account's import) and not added."""
    when = when or as_of
    if when < as_of:
        raise ValueError("a purchase cannot be in the past")
    if purchase < 0 or buffer < 0:
        raise ValueError("a purchase and a buffer are never negative")
    if isinstance(spend, Insufficient):
        return spend
    ahead = sorted({d for r in flow.income for d in r.dates if d > as_of})
    payday = next((d for d in ahead if d > when), None)
    if payday is None:
        return Insufficient(0, 1)  # no payday projected after the purchase
    month = {m.month: m for m in flow.months}
    moves: Counter[date] = Counter()
    for sign, series in ((-1, flow.recurring), (1, flow.income)):
        for r in series:
            per = Counter(f"{d:%Y-%m}" for d in r.dates)
            for d in r.dates:
                if sign > 0 and paid_to is not None and d <= paid_to:
                    continue
                i = list(month).index(f"{d:%Y-%m}")
                moves[d] += sign * r.months[i] // per[f"{d:%Y-%m}"]
    moves[when] -= purchase
    b, starts = spend.baseline, [spend.last_payday, *ahead]
    now = low = high = floor = balance - (purchase if when == as_of else 0)
    low_on, d = as_of, as_of
    while (d := d + timedelta(days=1)) < payday:
        start = max(p for p in starts if p <= d)
        length = (min(p for p in ahead if p > d) - start).days
        m = month[f"{d:%Y-%m}"]
        band = (m.total.upper - m.recurring) * length // _days(d)
        k = (d - start).days
        now += moves[d] - _spent(b, b.cycle, k)
        high += moves[d] - _spent(b, max(b.cycle, band), k)
        if now < low:
            low, low_on = now, d
        floor = min(floor, high)
    risk: Risk = "below_zero" if low < 0 else "below_buffer" if low < buffer else "none"
    return Outlook(payday, low, low_on, risk, floor)


def _spent(b: PaydayBaseline, cycle: int, k: int) -> int:
    """Pence of a `cycle`'s spend expected on day `k` of it, at `b`'s pace."""
    if not b.cycle:
        return 0
    before = cycle * b.spent_by(k - 1) // b.cycle if k else 0
    return cycle * b.spent_by(k) // b.cycle - before


def _days(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def shortfall(
    balance: int, as_of: date, flow: CashFlow, spend: Pace | Insufficient
) -> Insight | Insufficient | None:
    """A `shortfall` Insight, keyed by the payday it comes before, when the
    balance's band dips below zero first; impact is the projected deficit."""
    o = outlook(balance, as_of, flow, spend)
    if isinstance(o, Insufficient):
        return o
    if o.risk != "below_zero":
        return None
    return Insight("shortfall", f"{o.payday}", as_of, -o.minimum, CONF_SHORTFALL, ())
