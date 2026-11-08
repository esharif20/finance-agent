"""Shortfall risk before payday and what-if purchases (#151). Synthetic data
only: a hand-built forecast whose projection can be worked by hand."""

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.engine.baselines import Insufficient, PaydayBaseline
from finance_agent.engine.detectors import Insight
from finance_agent.engine.forecast import (
    Band,
    CashFlow,
    CashFlowMonth,
    RecurringPayment,
)
from finance_agent.engine.shortfall import Outlook, Pace, outlook, shortfall

AS_OF = date(2026, 3, 10)
PAYDAY = date(2026, 3, 25)
# 1,000p a day of discretionary spend, every day of a 28-day cycle
PACE = Pace(
    PaydayBaseline(tuple(1_000 * (k + 1) for k in range(28)), 28_000, 6),
    date(2026, 2, 25),
)
RENT = RecurringPayment(
    "SYNTH RENT", "monthly", (50_000, 50_000), (date(2026, 3, 15), date(2026, 4, 15))
)
SALARY = RecurringPayment(
    "SYNTH ACME SALARY", "monthly", (200_000, 200_000), (PAYDAY, date(2026, 4, 25))
)


def _flow(
    upper: int = 28_000, income: tuple[RecurringPayment, ...] = (SALARY,)
) -> CashFlow:
    months = tuple(
        CashFlowMonth(m, 50_000, Band(78_000, 70_000, 50_000 + upper), 200_000)
        for m in ("2026-03", "2026-04")
    )
    return CashFlow(months, (RENT,), {}, income)


def test_a_deficit_before_payday_is_a_shortfall_insight() -> None:
    # 14 days of 1,000p (11th to 24th) and the rent on the 15th: 60,000 - 64,000
    o = outlook(60_000, AS_OF, _flow(), PACE)
    assert o == Outlook(PAYDAY, -4_000, date(2026, 3, 24), "below_zero", -4_000)
    assert shortfall(60_000, AS_OF, _flow(), PACE) == Insight(
        "shortfall", "2026-03-25", AS_OF, 4_000, 80, ()
    )
    assert shortfall(70_000, AS_OF, _flow(), PACE) is None


def test_a_purchase_and_a_buffer() -> None:
    o = outlook(70_000, AS_OF, _flow(), PACE, 3_000, date(2026, 3, 12), buffer=10_000)
    assert o == Outlook(PAYDAY, 3_000, date(2026, 3, 24), "below_buffer", 3_000)
    o = outlook(70_000, AS_OF, _flow(), PACE, buffer=5_000)
    assert isinstance(o, Outlook) and o.risk == "none"
    # bought today: counted at once
    o = outlook(70_000, AS_OF, _flow(), PACE, 80_000)
    assert o == Outlook(PAYDAY, -74_000, date(2026, 3, 24), "below_zero", -74_000)


def test_a_purchase_after_payday_runs_to_the_payday_after() -> None:
    # the window runs on past the salary on 25 March to 24 April
    o = outlook(70_000, AS_OF, _flow(), PACE, 1_000, date(2026, 3, 30))
    assert isinstance(o, Outlook) and o.payday == date(2026, 4, 25)
    assert o.minimum == 6_000 and o.on == date(2026, 3, 24)  # before the salary


def test_the_floor_takes_the_top_of_the_forecast_band() -> None:
    # 62,000p a month at the top: 56,000p over the 28-day cycle, 2,000p a day
    o = outlook(60_000, AS_OF, _flow(upper=62_000), PACE)
    assert isinstance(o, Outlook) and (o.minimum, o.floor) == (-4_000, -18_000)


def test_no_forecast_is_insufficient_never_a_guess() -> None:
    assert outlook(60_000, AS_OF, _flow(), Insufficient(3, 6)) == Insufficient(3, 6)
    assert outlook(60_000, AS_OF, _flow(income=()), PACE) == Insufficient(0, 1)
    late = date(2026, 4, 25)  # no payday in the forecast after it
    assert outlook(60_000, AS_OF, _flow(), PACE, 1_000, late) == Insufficient(0, 1)
    assert shortfall(-1, AS_OF, _flow(), Insufficient(0, 6)) == Insufficient(0, 6)


def test_past_dates_and_negative_amounts_are_refused() -> None:
    with pytest.raises(ValueError):
        outlook(60_000, AS_OF, _flow(), PACE, 1_000, AS_OF - timedelta(days=1))
    with pytest.raises(ValueError):
        outlook(60_000, AS_OF, _flow(), PACE, -1)
    with pytest.raises(ValueError):
        outlook(60_000, AS_OF, _flow(), PACE, buffer=-1)


def test_extreme_amounts_stay_exact_integers() -> None:
    huge = 10**30
    o = outlook(huge, AS_OF, _flow(upper=huge), PACE, huge, date(2026, 3, 20))
    assert isinstance(o, Outlook)
    assert o.minimum == -64_000 and o.on == date(2026, 3, 24)
    assert all(type(v) is int for v in (o.minimum, o.floor))


days = st.integers(0, 40).map(lambda n: AS_OF + timedelta(days=n))
pence = st.integers(0, 10**12)


@settings(max_examples=80, deadline=None)
@given(st.integers(-(10**9), 10**12), pence, pence, days, st.integers(0, 10**10))
def test_a_bigger_purchase_never_raises_the_minimum(
    balance: int, a: int, b: int, when: date, upper: int
) -> None:
    small, big = sorted((a, b))
    lo = outlook(balance, AS_OF, _flow(upper), PACE, big, when)
    hi = outlook(balance, AS_OF, _flow(upper), PACE, small, when)
    assert isinstance(lo, Outlook) and isinstance(hi, Outlook)
    assert lo.minimum <= hi.minimum and lo.floor <= hi.floor
    assert lo.floor <= lo.minimum  # the band's bottom is never above the middle


@settings(max_examples=40, deadline=None)
@given(st.integers(-(10**9), 10**12), pence, days)
def test_the_outlook_is_deterministic(balance: int, amount: int, when: date) -> None:
    runs = {outlook(balance, AS_OF, _flow(), PACE, amount, when) for _ in range(3)}
    assert len(runs) == 1


def test_income_already_held_is_not_credited_again() -> None:
    """Verifier #227: a fresher account's import already holds the salary, so
    a stale `as_of` must not add it a second time (that would be optimistic)."""
    when = date(2026, 3, 30)
    o = outlook(70_000, AS_OF, _flow(), PACE, 1_000, when)
    held = outlook(70_000, AS_OF, _flow(), PACE, 1_000, when, paid_to=PAYDAY)
    assert isinstance(o, Outlook) and isinstance(held, Outlook)
    assert (o.minimum, held.minimum) == (6_000, -73_000)  # no salary on the 25th


@settings(max_examples=60, deadline=None)
@given(
    st.integers(0, 60), st.integers(0, 10**6), st.integers(0, 10**6), st.integers(0, 40)
)
def test_verifier_crediting_less_income_is_never_optimistic(
    days: int, balance: int, purchase: int, ahead: int
) -> None:
    paid_to = AS_OF + timedelta(days=days)
    when = AS_OF + timedelta(days=ahead)
    o = outlook(balance, AS_OF, _flow(), PACE, purchase, when)
    held = outlook(balance, AS_OF, _flow(), PACE, purchase, when, paid_to=paid_to)
    assert type(o) is type(held)
    if isinstance(o, Outlook) and isinstance(held, Outlook):
        assert held.minimum <= o.minimum and held.floor <= o.floor
        assert held.payday == o.payday
