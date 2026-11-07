"""Recurring detection (#104): a pure function over (date, minor units) charges."""

import random
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.engine.recurring import Recurrence, detect_recurring

D0 = date(2026, 1, 15)


def monthly(n: int, start: date = D0) -> list[date]:
    out: list[date] = []
    for i in range(n):
        m = start.month - 1 + i
        out.append(date(start.year + m // 12, m % 12 + 1, min(start.day, 28)))
    return out


def test_a_monthly_charge_is_found_with_its_cadence_and_count() -> None:
    r = detect_recurring([(d, 1099) for d in monthly(4)])
    assert r == Recurrence("monthly", 4, monthly(4)[0], monthly(4)[-1], 1099)


def test_weekly_and_annual_cadences() -> None:
    weekly = [(D0 + timedelta(weeks=i), 500) for i in range(5)]
    annual = [(date(2022 + i, 3, 1), 9900) for i in range(3)]
    assert (r := detect_recurring(weekly)) and r.interval == "weekly"
    assert (r := detect_recurring(annual)) and r.interval == "annual"


def test_two_charges_are_not_enough() -> None:
    assert detect_recurring([(d, 1099) for d in monthly(2)]) is None


@given(
    st.integers(3, 12),
    st.integers(100, 100_000),
    st.lists(st.integers(-2, 2), min_size=12, max_size=12),
    st.lists(st.integers(-2, 2), min_size=12, max_size=12),
)
def test_a_planted_monthly_charge_with_jitter_is_found(
    n: int, amount: int, days: list[int], pct: list[int]
) -> None:
    charges = [
        (d + timedelta(days=j), amount + amount * p // 100)
        for d, j, p in zip(monthly(n), days, pct, strict=False)
    ]
    r = detect_recurring(charges)
    assert r is not None and r.interval == "monthly" and r.charge_count == n


@given(
    st.lists(
        st.tuples(st.integers(0, 2000), st.integers(-50_000, 50_000)), max_size=20
    ),
    st.randoms(use_true_random=False),
)
def test_detection_is_deterministic_and_invariant_to_row_order(
    rows: list[tuple[int, int]], rnd: random.Random
) -> None:
    charges = [(D0 + timedelta(days=d), a) for d, a in rows]
    shuffled = charges[:]
    rnd.shuffle(shuffled)
    assert detect_recurring(charges) == detect_recurring(shuffled)


OFF_CADENCE = st.one_of(
    st.integers(1, 4), st.integers(10, 23), st.integers(37, 349), st.integers(381, 900)
)


@given(
    st.lists(st.integers(1, 900), min_size=1, max_size=10),
    OFF_CADENCE,
    st.integers(0, 10),
    st.integers(100, 100_000),
)
def test_irregular_payments_are_never_flagged(
    gaps: list[int], odd: int, at: int, amount: int
) -> None:
    gaps.insert(at % (len(gaps) + 1), odd)  # at least one gap fits no cadence
    days = [0]
    for g in gaps:
        days.append(days[-1] + g)
    assert detect_recurring([(D0 + timedelta(days=d), amount) for d in days]) is None


@given(st.lists(st.integers(1, 100_000), min_size=3, max_size=10))
def test_amounts_outside_five_percent_of_the_typical_charge_are_not_recurring(
    amounts: list[int],
) -> None:
    amounts.append(max(amounts) * 2)  # one charge far off the median
    charges = [
        (d, a) for d, a in zip(monthly(len(amounts)), sorted(amounts), strict=True)
    ]
    r = detect_recurring(charges)
    assert r is None or all(
        abs(a - r.typical_minor) * 20 <= r.typical_minor for a in amounts
    )


def test_adversarial_drifting_amounts_are_not_recurring() -> None:
    # +4% a month: each step looks stable, the series is not
    amounts = [1000, 1040, 1082, 1125, 1170, 1217]
    assert detect_recurring(list(zip(monthly(6), amounts, strict=True))) is None


def test_adversarial_refunds_in_the_series_are_ignored() -> None:
    charges = [(d, 1099) for d in monthly(4)]
    charges.append((monthly(4)[1] + timedelta(days=3), -1099))  # a refund
    r = detect_recurring(charges)
    assert r is not None and r.charge_count == 4


def test_refunds_alone_never_recur() -> None:
    assert detect_recurring([(d, -1099) for d in monthly(6)]) is None


def test_two_charges_on_one_day_break_the_cadence() -> None:
    charges = [(d, 1099) for d in monthly(4)] + [(monthly(4)[2], 1099)]
    assert detect_recurring(charges) is None
