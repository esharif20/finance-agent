"""Recurring detection (#104): a pure function over (date, minor units) charges."""

import random
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.categorise.recurring import CADENCES, Recurrence, detect_recurring

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
    st.integers(0, 1),
    st.integers(100, 100_000),
)
def test_a_latest_run_broken_by_an_irregular_gap_is_never_flagged(
    gaps: list[int], odd: int, back: int, amount: int
) -> None:
    gaps.insert(len(gaps) - back, odd)  # one of the last two gaps fits no cadence
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


def test_an_old_skipped_month_does_not_block_the_current_run() -> None:
    days = monthly(8)
    del days[2]  # skipped long ago; the last five charges are a clean run
    r = detect_recurring([(d, 1099) for d in days])
    assert r == Recurrence("monthly", 5, days[2], days[-1], 1099)


NOISE = st.lists(
    st.tuples(st.integers(0, 1500), st.integers(-50_000, 50_000)), max_size=15
)


@given(
    NOISE,
    st.integers(3, 12),
    st.integers(100, 100_000),
    st.integers(1, 200),
    st.randoms(use_true_random=False),
)
def test_a_noisy_prefix_followed_by_a_clean_run_is_detected_in_any_row_order(
    noise: list[tuple[int, int]], n: int, amount: int, pause: int, rnd: random.Random
) -> None:
    # adversarial: any old history (off-cadence days, other prices, refunds)
    # ending at least one day before a clean monthly run of n charges
    # charges at the run's own price off its cadence are evidence, not noise
    noise = [(d, a) for d, a in noise if abs(a - amount) * 10 > amount]
    start = D0 + timedelta(days=max((d for d, _ in noise), default=0) + pause)
    run = [(d, amount) for d in monthly(n, start)]
    charges = [(D0 + timedelta(days=d), a) for d, a in noise] + run
    r = detect_recurring(charges)
    assert r is not None and r.interval == "monthly"
    assert r.charge_count >= n and r.last_on == run[-1][0]
    assert abs(amount - r.typical_minor) * 20 <= r.typical_minor
    rnd.shuffle(charges)
    assert detect_recurring(charges) == r


@given(NOISE, st.integers(1, 2), st.integers(100, 100_000), OFF_CADENCE)
def test_a_latest_run_shorter_than_three_charges_is_not_detected(
    noise: list[tuple[int, int]], n: int, amount: int, odd: int
) -> None:
    last = max((d for d, _ in noise), default=0)
    prefix = [(D0 + timedelta(days=d), a) for d, a in noise]
    prefix.append((D0 + timedelta(days=last + 1), amount * 3))  # a price far off
    start = D0 + timedelta(days=last + 1 + odd)  # then an off-cadence gap
    charges = prefix + [(d, amount) for d in monthly(n, start)]
    assert detect_recurring(charges) is None


@given(NOISE)
def test_detection_is_deterministic(rows: list[tuple[int, int]]) -> None:
    charges = [(D0 + timedelta(days=d), a) for d, a in rows]
    assert detect_recurring(charges) == detect_recurring(list(charges))


def test_a_price_step_starts_a_new_run() -> None:
    days = monthly(7)
    charges = [(d, 1099) for d in days[:3]] + [(d, 1299) for d in days[3:]]
    assert detect_recurring(charges) == Recurrence(
        "monthly", 4, days[3], days[-1], 1299
    )


def test_a_price_step_with_fewer_than_three_new_charges_is_not_detected() -> None:
    days = monthly(6)
    charges = [(d, 1099) for d in days[:4]] + [(d, 1299) for d in days[4:]]
    assert detect_recurring(charges) is None


def test_one_price_bought_more_often_than_the_cadence_is_not_a_series() -> None:
    # adversarial (verifier #183): café visits at one price whose last three
    # happen to be a week apart; a series is never charged sooner than its cadence
    days, d = [], D0
    for g in (3, 4, 2, 3, 7, 7):
        d += timedelta(days=g)
        days.append(d)
    assert detect_recurring([(d, 350) for d in days]) is None


@given(st.integers(3, 8), st.integers(100, 100_000), OFF_CADENCE, st.integers(0, 3))
def test_an_earlier_same_price_charge_off_the_cadence_blocks_the_run(
    n: int, amount: int, odd: int, shift: int
) -> None:
    run = monthly(n, D0 + timedelta(days=1000))
    early = run[0] - timedelta(days=odd)
    # unless the gap is whole missed months, the same price off cadence is ad hoc
    if odd >= 24 and abs(odd - (odd + 15) // 30 * 30) <= 6:
        return
    charges = [(early, amount + amount * shift // 100)] + [(d, amount) for d in run]
    assert detect_recurring(charges) is None


@given(
    st.integers(3, 8),
    st.integers(3, 8),
    st.integers(100, 50_000),
    st.integers(11, 300),
    st.integers(0, 27),
    st.randoms(use_true_random=False),
)
def test_two_interleaved_series_at_different_prices_never_merge(
    n: int, m: int, low: int, rise_pct: int, offset: int, rnd: random.Random
) -> None:
    # adversarial (verifier #183): two monthly plans from one Merchant
    high = low + low * rise_pct // 100
    a = [(d, low) for d in monthly(n)]
    b = [(d + timedelta(days=offset), high) for d in monthly(m)]
    charges = a + b
    rnd.shuffle(charges)
    r = detect_recurring(charges)
    if r is not None:
        price, count = (
            (low, n)
            if abs(r.typical_minor - low) < abs(r.typical_minor - high)
            else (high, m)
        )
        assert r.typical_minor == price and r.charge_count <= count


@given(
    st.lists(st.tuples(st.integers(0, 1500), st.integers(-50_000, 50_000)), max_size=25)
)
def test_a_flagged_run_is_contiguous_on_cadence_and_within_the_band(
    rows: list[tuple[int, int]],
) -> None:
    charges = [(D0 + timedelta(days=d), a) for d, a in rows]
    r = detect_recurring(charges)
    if r is None:
        return
    run = sorted((d, a) for d, a in charges if a > 0 and r.first_on <= d <= r.last_on)
    lo, hi = CADENCES[r.interval]
    assert len(run) == r.charge_count >= 3
    assert all(lo <= (y[0] - x[0]).days <= hi for x, y in zip(run, run[1:]))
    assert all(abs(a - r.typical_minor) * 20 <= r.typical_minor for _, a in run)
