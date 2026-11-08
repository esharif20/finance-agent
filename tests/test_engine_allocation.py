"""Allocation Plans (#260): the LP splits income exactly, holds every hard
limit, ignores input order and reports infeasible mixes."""

import random
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.engine.allocation import (
    MAX_MINOR,
    AllocationInput,
    Bill,
    Category,
    Goal,
    Infeasible,
    Kind,
    Plan,
    Reason,
    fifty_thirty_twenty,
    plan,
    split,
    violations,
)

pence = st.integers(0, 2_000_000)


@st.composite
def inputs(draw: st.DrawFn) -> AllocationInput:
    income = draw(st.integers(0, 3_000_000))
    bills = tuple(
        Bill(f"b{i}", a) for i, a in enumerate(draw(st.lists(pence, max_size=3)))
    )
    goals = tuple(
        Goal(
            f"g{i}",
            draw(pence),
            draw(st.integers(0, 300_000)),
            draw(st.none() | pence),
            draw(st.sampled_from([1.0, 2.0, 3.0])),
        )
        for i in range(draw(st.integers(0, 3)))
    )
    cats = tuple(
        Category(
            f"c{i}",
            draw(pence),
            draw(st.integers(0, 300_000)),
            draw(st.none() | pence),
            draw(st.sampled_from([1.0, 2.0, 3.0])),
            draw(st.booleans()),
        )
        for i in range(draw(st.integers(0, 6)))
    )
    return AllocationInput(income, bills, goals, cats)


def _feasible(inp: AllocationInput) -> bool:
    spare = inp.income_minor - sum(b.amount_minor for b in inp.bills)
    bounds = [(x.floor_minor, x.cap_minor) for x in (*inp.goals, *inp.categories)]
    return (
        spare >= 0
        and all(hi is None or lo <= hi for lo, hi in bounds)
        and sum(lo for lo, _ in bounds) <= spare
    )


@settings(max_examples=300, deadline=None)
@given(inputs())
def test_plan_sums_to_income_and_holds_every_constraint(inp: AllocationInput) -> None:
    result = plan(inp)
    if not _feasible(inp):
        assert isinstance(result, Infeasible)
        return
    assert isinstance(result, Plan)
    assert sum(ln.amount_minor for ln in result.lines) == inp.income_minor
    assert violations(inp, result) == []
    assert all(type(ln.amount_minor) is int for ln in result.lines)


@settings(max_examples=100, deadline=None)
@given(inputs(), st.randoms(use_true_random=False))
def test_plan_is_invariant_to_category_order(
    inp: AllocationInput, rng: random.Random
) -> None:
    other = replace(
        inp,
        bills=tuple(rng.sample(inp.bills, len(inp.bills))),
        goals=tuple(rng.sample(inp.goals, len(inp.goals))),
        categories=tuple(rng.sample(inp.categories, len(inp.categories))),
    )
    assert plan(other) == plan(inp)


@settings(max_examples=100, deadline=None)
@given(inputs(), st.sampled_from([1, -1]))
def test_plan_is_stable_under_one_percent_income(
    inp: AllocationInput, sign: int
) -> None:
    step = inp.income_minor // 100
    a, b = plan(inp), plan(replace(inp, income_minor=inp.income_minor + sign * step))
    if isinstance(a, Plan) and isinstance(b, Plan):
        moved = sum(
            abs(x.amount_minor - y.amount_minor)
            for x, y in zip(a.lines, b.lines, strict=True)
        )
        assert moved <= step + 2  # only the marginal lines move, plus a penny each way


@settings(max_examples=100, deadline=None)
@given(inputs())
def test_baseline_sums_to_income(inp: AllocationInput) -> None:
    result = fifty_thirty_twenty(inp)
    if isinstance(result, Plan):
        assert sum(ln.amount_minor for ln in result.lines) == inp.income_minor


def test_caps_bills_and_leftover_are_explained() -> None:
    inp = AllocationInput(
        300_000,
        (Bill("rent", 150_000),),
        (Goal("emergency_fund", 30_000, 20_000, weight=1.0),),
        (
            Category("groceries", 40_000, 30_000, weight=3.0, essential=True),
            Category("takeaway", 30_000, cap_minor=15_000, weight=1.0),
            Category("shopping", 60_000, weight=2.0),
        ),
    )
    result = plan(inp)
    assert isinstance(result, Plan)
    by = {ln.name: ln for ln in result.lines}
    assert by["rent"].reason is Reason.FIXED_BILL
    assert by["takeaway"].amount_minor == 15_000
    assert by["takeaway"].reason is Reason.CONSTRAINT
    assert by["groceries"].amount_minor == 40_000  # top priority kept
    assert by["groceries"].reason is Reason.ON_PATTERN
    # 150,000 spare; 40,000 + 15,000 + 60,000 = 115,000; goal gets 30,000; 5,000 left
    assert by["emergency_fund"].reason is Reason.ON_PATTERN
    assert by["unallocated"].amount_minor == 5_000
    assert by["unallocated"].reason is Reason.LEFTOVER


def test_goal_floor_holds_when_money_is_short() -> None:
    inp = AllocationInput(
        100_000,
        (),
        (Goal("car", 50_000, 40_000, weight=1.0),),
        (Category("shopping", 80_000, weight=3.0),),
    )
    result = plan(inp)
    assert isinstance(result, Plan)
    by = {ln.name: ln for ln in result.lines}
    assert (by["car"].amount_minor, by["car"].reason) == (40_000, Reason.GOAL_FLOOR)
    assert (by["shopping"].amount_minor, by["shopping"].reason) == (
        60_000,
        Reason.PRIORITY,
    )


def test_rounding_splits_pennies_exactly() -> None:
    # 100p over three equal lines with no pattern: the LP gives fractions
    inp = AllocationInput(
        100, (), (), tuple(Category(f"c{i}", 50, weight=1.0) for i in range(3))
    )
    result = plan(inp)
    assert isinstance(result, Plan)
    assert sum(ln.amount_minor for ln in result.lines) == 100
    assert violations(inp, result) == []


# Adversarial


@pytest.mark.parametrize(
    ("inp", "reason"),
    [
        (AllocationInput(1_000, (Bill("rent", 1_001),)), "bills_exceed_income"),
        (
            AllocationInput(1_000, (), (Goal("car", 0, 600), Goal("trip", 0, 401))),
            "floors_exceed_income",
        ),
        (
            AllocationInput(1_000, (), (), (Category("food", 100, 50, 49),)),
            "floor_above_cap",
        ),
        (AllocationInput(0, (Bill("rent", 1),)), "bills_exceed_income"),
        (AllocationInput(0, (), (Goal("car", 10, 1),)), "floors_exceed_income"),
    ],
)
def test_infeasible_constraints_are_reported(inp: AllocationInput, reason: str) -> None:
    assert plan(inp) == Infeasible(reason)


def test_zero_income_with_nothing_owed_is_an_all_zero_plan() -> None:
    inp = AllocationInput(0, (), (Goal("car", 5_000),), (Category("food", 9_000),))
    result = plan(inp)
    assert isinstance(result, Plan)
    assert all(ln.amount_minor == 0 for ln in result.lines)


@pytest.mark.parametrize("income", [MAX_MINOR, MAX_MINOR - 7, 10**13 + 1])
def test_extreme_amounts_stay_exact(income: int) -> None:
    inp = AllocationInput(
        income,
        (Bill("rent", income // 3),),
        (Goal("fund", income // 7, income // 11),),
        (
            Category("food", income // 5, income // 13, weight=3.0),
            Category("fun", income // 2, cap_minor=income // 9),
        ),
    )
    result = plan(inp)
    assert isinstance(result, Plan)
    assert sum(ln.amount_minor for ln in result.lines) == income
    assert violations(inp, result) == []


@pytest.mark.parametrize(
    "inp",
    [
        AllocationInput(-1),
        AllocationInput(MAX_MINOR + 1),
        AllocationInput(100, (Bill("rent", -5),)),
        AllocationInput(100, (), (), (Category("a", 1), Category("a", 2))),
        AllocationInput(100, (), (), (Category("a", 1, weight=0.0),)),
        AllocationInput(100, (), (), (Category("a", 1, weight=float("nan")),)),
        AllocationInput(100, buffer_weight=-1.0),
    ],
)
def test_bad_input_is_rejected(inp: AllocationInput) -> None:
    with pytest.raises(ValueError):
        plan(inp)


def test_violations_catch_a_tampered_plan() -> None:
    inp = AllocationInput(
        1_000, (Bill("rent", 400),), (), (Category("food", 600, 500),)
    )
    good = plan(inp)
    assert isinstance(good, Plan)
    lines = [
        replace(ln, amount_minor=499) if ln.kind is Kind.CATEGORY else ln
        for ln in good.lines
    ]
    assert violations(inp, Plan(1_000, tuple(lines))) == [
        "lines do not sum to income",
        "category food below its floor",
    ]


@given(st.integers(0, 10**12), st.lists(st.integers(0, 10**6), max_size=8))
def test_split_is_exact(total: int, weights: list[int]) -> None:
    out = split(total, weights)
    assert sum(out) == (total if weights else 0)
    assert all(x >= 0 for x in out)
