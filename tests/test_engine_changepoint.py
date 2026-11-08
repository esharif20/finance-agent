"""Change-point detection (#256): the nine-month gate, row-order invariance,
one-off spikes, and a level shift's month, levels and evidence."""

from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.engine.baselines import Insufficient
from finance_agent.engine.changepoint import (
    MIN_MONTHS,
    MIN_REFERENCE,
    LifeChange,
    Row,
    alarms,
    detect_changes,
)


def month(i: int, day: int = 1) -> date:
    return date(2024 + i // 12, i % 12 + 1, day)


def rent(levels: list[int]) -> list[Row]:
    return [(i + 1, month(i), -pence) for i, pence in enumerate(levels)]


def test_below_nine_months_is_insufficient() -> None:
    assert detect_changes(rent([100_000] * 8), "rent") == Insufficient(8, MIN_MONTHS)
    assert detect_changes([], "rent") == Insufficient(0, MIN_MONTHS)
    # a gap month counts as a month with nothing in it
    rows = [r for r in rent([100_000] * 9) if r[1] != month(4)]
    assert detect_changes(rows[:7], "rent") == Insufficient(8, MIN_MONTHS)
    assert detect_changes(rows, "rent") == ()


def test_a_rent_rise_reports_its_month_levels_and_evidence() -> None:
    rows = rent([100_000] * 12 + [115_000] * 6)
    assert detect_changes(rows, "rent") == (
        LifeChange("rent", month(12), month(14), 100_000, 115_000, (13, 14, 15)),
    )


def test_income_counts_credits_and_through_stops_the_series() -> None:
    rows = [(i + 1, month(i, 25), 300_000) for i in range(12)]
    rows += [(13 + i, month(12 + i, 25), 90_000) for i in range(4)]  # job loss
    found = detect_changes(rows, "income")
    assert not isinstance(found, Insufficient) and len(found) == 1
    change = found[0]
    assert (change.month, change.before, change.after) == (month(12), 300_000, 90_000)
    assert detect_changes(rows, "income", through=month(13)) == ()
    # the months after the last credit count as zero income
    assert detect_changes(rows[:12], "income", through=month(16)) != ()


levels = st.lists(st.integers(0, 500_000), min_size=1, max_size=40)


@given(levels)
def test_never_reports_before_nine_months(values: list[int]) -> None:
    for change, detected in alarms(values):
        assert detected >= MIN_MONTHS - 1
        assert MIN_REFERENCE <= change <= detected - 2  # three months at the new level
    found = detect_changes(rent(values), "rent")
    if len(values) < MIN_MONTHS:
        assert found == Insufficient(len(values), MIN_MONTHS)


@given(levels, st.randoms(use_true_random=False))
def test_invariant_to_row_order(values: list[int], rng: object) -> None:
    rows = rent(values) + [(1_000 + i, month(i, 20), -7) for i in range(len(values))]
    shuffled = list(rows)
    rng.shuffle(shuffled)  # type: ignore[attr-defined]
    assert detect_changes(shuffled, "rent") == detect_changes(rows, "rent")


@given(
    st.integers(50_000, 200_000),
    st.integers(0, 35),
    st.integers(2, 1_000),
    st.booleans(),
)
def test_a_one_off_spike_or_dip_never_reports(
    level: int, at: int, times: int, dip: bool
) -> None:
    """Adversarial: one month of any size, up or down to zero, on a flat bill."""
    values = [level] * 36
    values[at] = 0 if dip else level * times
    assert detect_changes(rent(values), "rent") == ()
