"""Coaching goals (#274): the weekly target recomputes from progress, never
goes negative, is "overdue" past its date, and is checked against the plan."""

import json
from datetime import date, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

import coaching
import gate
import synthetic_goals as sg
from finance_agent.engine import coaching as co

START, DUE = date(2026, 1, 5), date(2026, 3, 30)  # 12 weeks


def test_target_spreads_what_is_left_over_the_weeks_left() -> None:
    t = co.weekly_target(120_000, START, DUE, START, 0, estimated=False)
    assert (t.weekly_target_minor, t.weeks_left, t.status) == (
        10_000,
        12,
        co.Status.UNCHECKED,
    )
    # a missed week: the same remaining money over fewer weeks, rounded up
    later = co.weekly_target(
        120_000, START, DUE, START + timedelta(days=14), 10_001, estimated=False
    )
    assert later.weekly_target_minor == 11_000  # ceil(109_999 / 10)


def test_at_risk_carries_the_weekly_shortfall_against_the_plan_line() -> None:
    # £400 a month is 400*12//52 = £92.30 a week, below the £100 target
    t = co.weekly_target(
        120_000, START, DUE, START, 0, estimated=True, plan_monthly_minor=40_000
    )
    assert t.status is co.Status.AT_RISK
    assert t.estimated
    assert (t.planned_weekly_minor, t.shortfall_minor) == (9_230, 770)
    ok = co.weekly_target(
        120_000, START, DUE, START, 0, estimated=False, plan_monthly_minor=43_334
    )
    assert ok.status is co.Status.ON_TRACK
    assert ok.shortfall_minor is None


def test_a_past_due_date_is_overdue_not_a_figure() -> None:
    for today in (DUE, DUE + timedelta(days=40)):
        t = co.weekly_target(120_000, START, DUE, today, 5_000, estimated=False)
        assert t.status is co.Status.OVERDUE
        assert t.weekly_target_minor is None
        assert t.remaining_minor == 115_000


def test_reached_wins_over_overdue_and_targets_nothing() -> None:
    t = co.weekly_target(
        120_000, START, DUE, DUE + timedelta(1), 130_000, estimated=False
    )
    assert (t.status, t.weekly_target_minor, t.remaining_minor) == (
        co.Status.REACHED,
        0,
        0,
    )


def test_progress_sources() -> None:
    assert co.linked_saved(50_000, 42_000) == -8_000  # a withdrawal counts
    assert co.estimated_saved(30_000, 34_000) == -4_000  # overspend counts


@pytest.mark.parametrize(
    ("amount", "due", "today", "plan"),
    [
        (0, DUE, START, None),
        (100, START, START, None),
        (100, DUE, START - timedelta(1), None),
        (100, DUE, START, -1),
    ],
)
def test_rejects_malformed_goals(
    amount: int, due: date, today: date, plan: int | None
) -> None:
    with pytest.raises(ValueError):
        co.weekly_target(
            amount, START, due, today, 0, estimated=False, plan_monthly_minor=plan
        )


goals = st.tuples(
    st.integers(1, 10**12),  # amount
    st.integers(1, 2_000),  # days from start to due
    st.integers(0, 2_500),  # days from start to today
    st.integers(-(10**12), 10**12),  # saved, negative after withdrawals
    st.none() | st.integers(0, 10**12),  # plan line, monthly
)


@given(goals)
def test_same_history_same_target_never_negative_and_enough(
    g: tuple[int, int, int, int, int | None],
) -> None:
    amount, span, elapsed, saved, plan = g
    due, today = START + timedelta(span), START + timedelta(elapsed)
    args = (amount, START, due, today, saved)
    t = co.weekly_target(*args, estimated=False, plan_monthly_minor=plan)
    assert t == co.weekly_target(*args, estimated=False, plan_monthly_minor=plan)
    assert t.remaining_minor >= 0
    if t.weekly_target_minor is None:
        assert t.status is co.Status.OVERDUE and today >= due
        return
    assert t.weekly_target_minor >= 0
    if t.weeks_left is not None:
        # paid every week left it reaches the goal, and a penny less does not
        w, x = t.weeks_left, t.weekly_target_minor
        assert x * w >= t.remaining_minor > (x - 1) * w
        assert (t.status is co.Status.AT_RISK) == (
            plan is not None and x > co.weekly_plan(plan)
        )


def test_dataset_covers_every_kind_with_both_risk_labels() -> None:
    ss = sg.scenarios()
    assert len(ss) >= 200
    assert sg.fingerprint(ss) == sg.fingerprint(sg.scenarios())
    assert {s.kind for s in ss} == set(sg.KINDS)
    risky = [coaching.truth(s)[1] for s in ss]
    assert 0 < sum(risky) < len(ss)


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "coaching",
            coaching.evaluate,
            coaching.RESULTS,
            coaching.lower_is_better,
            [],
        )
        == 0
    )


def test_engine_meets_its_gates_and_beats_the_fixed_target() -> None:
    models = json.loads(coaching.RESULTS.read_text())["models"]
    got, base = models["coaching"]["metrics"], models["fixed_target"]["metrics"]
    for name in (
        "target_exact",
        "feasibility_agreement",
        "at_risk_precision",
        "at_risk_recall",
        "overdue_reported",
    ):
        assert got[name] == 1.0
    assert got["negative_targets"] == 0
    assert got["target_exact"] > base["target_exact"]
    assert got["at_risk_recall"] > base["at_risk_recall"]
