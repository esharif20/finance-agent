"""Personal baselines per Category (#147, ADR 0015): a seasonal one and a
payday-cycle-aware one, each "insufficient" below its data minimum."""

import random
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.engine.baselines import (
    MIN_CYCLES,
    MIN_MONTHS,
    Insufficient,
    payday_baseline,
    seasonal_baseline,
)


def _monthly(first: date, pence: list[int]) -> list[tuple[date, int]]:
    """One charge on the 10th of each month from `first`."""
    out: list[tuple[date, int]] = []
    y, m = first.year, first.month
    for p in pence:
        out.append((date(y, m, 10), -p))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def test_below_six_months_the_seasonal_baseline_is_insufficient() -> None:
    rows = _monthly(date(2024, 1, 1), [1_000] * 5)
    assert seasonal_baseline(rows, date(2024, 6, 1)) == Insufficient(5, MIN_MONTHS)
    assert seasonal_baseline([], date(2024, 6, 1)) == Insufficient(0, MIN_MONTHS)


def test_with_six_months_it_is_the_median_month() -> None:
    # a one-month spike does not move the median
    rows = _monthly(date(2024, 1, 1), [1_000, 1_200, 900, 5_000, 1_100, 1_000])
    b = seasonal_baseline(rows, date(2024, 7, 1))
    assert not isinstance(b, Insufficient)
    assert b.expected == 1_050 and b.seasonal is False


def test_rows_on_or_after_the_month_are_ignored() -> None:
    rows = _monthly(date(2024, 1, 1), [1_000] * 6 + [99_999])
    b = seasonal_baseline(rows, date(2024, 7, 1))
    assert not isinstance(b, Insufficient) and b.expected == 1_000


def test_refunds_and_income_are_not_spending() -> None:
    rows = [*_monthly(date(2024, 1, 1), [1_000] * 6), (date(2024, 3, 2), 50_000)]
    b = seasonal_baseline(rows, date(2024, 7, 1))
    assert not isinstance(b, Insufficient) and b.expected == 1_000


def test_after_a_year_it_follows_the_same_month_last_year() -> None:
    # flat 1,000 a month except a 3,000 July: next July is expected at 3,000
    pence = [1_000] * 12
    pence[6] = 3_000
    rows = _monthly(date(2024, 1, 1), pence)
    b = seasonal_baseline(rows, date(2025, 7, 1))
    assert not isinstance(b, Insufficient)
    assert b.seasonal is True and b.expected == 3_000
    # and a quiet month stays at the level
    b = seasonal_baseline(rows, date(2025, 3, 1))
    assert not isinstance(b, Insufficient) and b.expected == 1_000


def _paydays(first: date, n: int) -> list[date]:
    return [first + timedelta(days=30 * i) for i in range(n)]


def test_below_six_pay_cycles_the_payday_baseline_is_insufficient() -> None:
    paydays = _paydays(date(2024, 1, 25), 6)  # five complete cycles
    rows = [(p + timedelta(days=1), -500) for p in paydays]
    assert payday_baseline(rows, paydays, paydays[-1]) == Insufficient(5, MIN_CYCLES)
    assert payday_baseline([], [], date(2024, 1, 1)) == Insufficient(0, MIN_CYCLES)


def test_the_payday_baseline_is_spend_so_far_into_a_cycle() -> None:
    paydays = _paydays(date(2024, 1, 25), 7)  # six complete cycles
    # each cycle: 3,000 on its first day, 1,000 on day 20
    rows = [r for p in paydays[:6] for r in ((p, -3_000), (p + timedelta(20), -1_000))]
    b = payday_baseline(rows, paydays, paydays[-1])
    assert not isinstance(b, Insufficient)
    assert b.spent_by(0) == 3_000
    assert b.spent_by(19) == 3_000
    assert b.spent_by(20) == 4_000
    assert b.spent_by(45) == b.cycle == 4_000
    assert b.cycles == 6


def test_a_charge_in_some_cycles_counts_as_its_share_not_as_nothing() -> None:
    paydays = _paydays(date(2024, 1, 25), 8)  # seven complete cycles
    # 1,000 a cycle: on payday in three cycles, ten days on in four
    rows = [(p + timedelta(0 if i < 3 else 10), -1_000) for i, p in enumerate(paydays)]
    b = payday_baseline(rows[:7], paydays, paydays[-1])
    assert not isinstance(b, Insufficient)
    assert b.spent_by(0) == 429  # 1,000 x 3/7, where a median would say 0
    assert b.spent_by(10) == b.cycle == 1_000


# --- properties ---------------------------------------------------------------

START = date(2023, 1, 1)
AMOUNT = st.integers(-(10**12), 10**12)  # extreme amounts, in pence
ROWS = st.lists(st.tuples(st.dates(START, date(2026, 12, 31)), AMOUNT), max_size=60)
PAYDAYS = st.lists(st.dates(START, date(2026, 12, 31)), max_size=40)
MONTH = st.dates(START, date(2027, 6, 1)).map(lambda d: d.replace(day=1))


@given(ROWS, MONTH, st.randoms())
def test_seasonal_is_deterministic_and_invariant_to_row_order(
    rows: list[tuple[date, int]], month: date, rnd: random.Random
) -> None:
    shuffled = rows[:]
    rnd.shuffle(shuffled)
    assert seasonal_baseline(rows, month) == seasonal_baseline(shuffled, month)
    assert seasonal_baseline(rows, month) == seasonal_baseline(rows, month)


@given(ROWS, MONTH)
def test_seasonal_is_insufficient_on_sparse_history_and_bounded_otherwise(
    rows: list[tuple[date, int]], month: date
) -> None:
    b = seasonal_baseline(rows, month)
    before = [(d, a) for d, a in rows if d < month]
    first = min((d for d, _ in before), default=None)
    span = (
        0
        if first is None
        else ((month.year - first.year) * 12 + month.month - first.month)
    )
    if span < MIN_MONTHS:
        assert b == Insufficient(span, MIN_MONTHS)
    else:
        assert not isinstance(b, Insufficient)
        assert 0 <= b.expected <= sum(max(0, -a) for _, a in before)


@given(ROWS, PAYDAYS, st.dates(START, date(2027, 6, 1)), st.randoms())
def test_payday_is_deterministic_and_invariant_to_row_order(
    rows: list[tuple[date, int]], paydays: list[date], as_of: date, rnd: random.Random
) -> None:
    shuffled, days = rows[:], paydays[:]
    rnd.shuffle(shuffled)
    rnd.shuffle(days)
    b = payday_baseline(rows, paydays, as_of)
    assert b == payday_baseline(shuffled, days, as_of)
    days = sorted({p for p in paydays if p <= as_of})
    first = min((d for d, _ in rows), default=None)
    # complete cycles that end after the Category's first Transaction
    complete = sum(1 for b in days[1:] if first is not None and b > first)
    if complete < MIN_CYCLES:
        assert b == Insufficient(complete, MIN_CYCLES)
    else:
        assert not isinstance(b, Insufficient)
        # spend so far never falls as the cycle goes on
        assert list(b.by_day) == sorted(b.by_day)
        assert all(0 <= x <= b.cycle for x in b.by_day)


# --- verifier (#175) ------------------------------------------------------------


def test_a_refunded_purchase_does_not_inflate_next_years_month() -> None:
    # the Ledger reports a Category's spending after Refunds; so does its baseline
    rows = _monthly(date(2024, 1, 1), [1_000] * 12)
    rows += [(date(2024, 7, 3), -40_000), (date(2024, 7, 9), 40_000)]
    b = seasonal_baseline(rows, date(2025, 7, 1))
    assert not isinstance(b, Insufficient) and b.expected == 1_000


def test_a_month_of_net_refunds_counts_as_nothing_spent() -> None:
    rows = _monthly(date(2024, 1, 1), [1_000] * 6) + [(date(2024, 6, 20), 9_000)]
    b = seasonal_baseline(rows, date(2024, 7, 1))
    # June nets to -8,000; it is a zero month, never a negative one
    assert not isinstance(b, Insufficient) and b.expected == 1_000


def test_a_new_category_has_no_payday_baseline_from_cycles_before_it() -> None:
    # six complete cycles of paydays, but the Category's first charge is in the last:
    # five cycles of zeros would otherwise be a fake baseline of 0
    paydays = _paydays(date(2024, 1, 25), 7)
    rows = [(paydays[5] + timedelta(days=3), -2_000)]
    assert payday_baseline(rows, paydays, paydays[-1]) == Insufficient(1, MIN_CYCLES)


@given(ROWS, ROWS, PAYDAYS, PAYDAYS, MONTH)
def test_a_baseline_at_t_never_sees_rows_or_paydays_after_t(
    rows: list[tuple[date, int]],
    later: list[tuple[date, int]],
    paydays: list[date],
    later_paydays: list[date],
    t: date,
) -> None:
    # adversarial: anything dated on or after t, however large, changes nothing
    future = [(max(d, t), a) for d, a in later]
    assert seasonal_baseline(rows, t) == seasonal_baseline(rows + future, t)
    days_after = [max(p, t + timedelta(days=1)) for p in later_paydays]
    assert payday_baseline(rows, paydays, t) == payday_baseline(
        rows + future, paydays + days_after, t
    )
