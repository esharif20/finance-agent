"""ETS forecasting with prediction intervals and cash flow (#149, ADR 0015).
Synthetic data only."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.engine.forecast import (
    MIN_MONTHS,
    Band,
    cash_flow,
    forecast_category,
)
from finance_agent.ledger import Ledger

SEASON = (100, 95, 100, 100, 100, 100, 110, 130, 100, 100, 105, 160)
SEASONAL = [30_000 * SEASON[m % 12] // 100 + 37 * (m * 7 % 11) for m in range(30)]

months = st.lists(st.integers(-10_000, 10**12), max_size=40)


def test_under_twelve_months_is_insufficient_never_a_crash() -> None:
    for history in ([], [1_200], [0] * 11, [500, 0, 700] * 3):
        f = forecast_category(history, 3)
        assert f.method == "insufficient" and f.months == ()


def test_seasonal_naive_below_the_ets_gate() -> None:
    # ADR 0015: seasonal naive from 12 months; the backtest set ETS at 24
    assert MIN_MONTHS == 24
    f = forecast_category(SEASONAL[:12], 2)
    assert f.method == "seasonal_naive"
    assert [b.point for b in f.months] == SEASONAL[0:2]
    f = forecast_category(SEASONAL[:23], 3)
    assert f.method == "seasonal_naive"
    assert [b.point for b in f.months] == SEASONAL[11:14]


def test_ets_follows_the_season_from_two_years() -> None:
    f = forecast_category(SEASONAL[:24], 12)
    assert f.method == "ets"
    december, january = f.months[11].point, f.months[0].point
    assert december > 1.4 * january  # the December peak is forecast
    assert all(b.lower < b.point < b.upper for b in f.months)  # real intervals


def test_a_constant_or_sparse_category_is_handled() -> None:
    flat = forecast_category([2_500] * 30, 3)
    assert [(b.point, b.lower, b.upper) for b in flat.months] == [(2_500,) * 3] * 3
    zeros = forecast_category([0] * 30, 3)
    assert (
        all(b == zeros.months[0] for b in zeros.months) and zeros.months[0].upper == 0
    )
    spike = forecast_category([0] * 26 + [9_000] + [0] * 3, 3)
    assert len(spike.months) == 3


@settings(max_examples=60, deadline=None)
@given(months, st.integers(1, 12))
def test_the_interval_contains_the_point_and_nothing_is_negative(
    history: list[int], horizon: int
) -> None:
    f = forecast_category(history, horizon)
    assert len(f.months) == (0 if f.method == "insufficient" else horizon)
    assert (f.method == "insufficient") == (len(history) < 12)
    for b in f.months:
        assert 0 <= b.lower <= b.point <= b.upper
        assert all(type(v) is int for v in (b.point, b.lower, b.upper))  # pence


@settings(max_examples=20, deadline=None)
@given(months, st.integers(1, 6))
def test_the_forecast_is_deterministic(history: list[int], horizon: int) -> None:
    assert forecast_category(history, horizon) == forecast_category(history, horizon)


@settings(max_examples=20, deadline=None)
@given(st.lists(st.sampled_from([0, 1, 10**15, 10**18]), min_size=24, max_size=36))
def test_extreme_amounts_do_not_overflow(history: list[int]) -> None:
    for b in forecast_category(history, 3).months:
        assert 0 <= b.lower <= b.point <= b.upper < 10**306


def _month(i: int, day: int) -> date:
    return date(2024 + i // 12, 1 + i % 12, day)


def test_cash_flow_projects_recurring_payments_and_forecasts_the_rest(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    rows: list[tuple[date, str, str]] = []
    for i in range(26):  # Jan 2024 to Feb 2026
        rows.append((_month(i, 5), "SYNTH IRONGYM", "-35.00"))
        rows.append((_month(i, 9), "SYNTH GROCER", f"-{180 + SEASON[i % 12]}.{i:02d}"))
        rows.append((_month(i, 21), "SYNTH GROCER", f"-{95 + i % 4 * 7}.50"))
        if i < 18:  # stopped in mid-2025: lapsed, never projected
            rows.append((_month(i, 12), "SYNTH OLDBOX", "-12.99"))
        rows.append((_month(i, 15), "SYNTH BOXCLUB", "-20.00"))
        if i >= 23:  # new Category: too little history to forecast
            rows.append((_month(i, 17), "SYNTH BEANERY", f"-{30 + i * 9 % 23}.00"))
    rows += [(date(y, 4, 2), "SYNTH CLOUDNOTE", "-59.00") for y in (2023, 2024, 2025)]
    text = "".join(f"{d:%d/%m/%Y},{desc},{amt}\n" for d, desc, amt in rows)
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([write_export(text)])
        categorise.run(conn, 1)
        led = Ledger(conn, 1)
        ids = {m.name: m.id for m in led.merchants()}
        led.correct_merchant(_id(ids, "GROCER"), ("Food & Drink", "Groceries"))
        led.correct_merchant(_id(ids, "BEANERY"), ("Food & Drink", "Coffee"))
        led.correct_merchant(_id(ids, "BOXCLUB"), ("Shopping", "Online"))
        led.not_a_subscription(_id(ids, "BOXCLUB"))  # the user says it is not one
        categorise.run(conn, 1)
        flow = cash_flow(Ledger(conn, 1), date(2026, 3, 14), 3)

    assert [m.month for m in flow.months] == ["2026-03", "2026-04", "2026-05"]
    gym = [r for r in flow.recurring if "IRONGYM" in r.merchant.upper()]
    assert [(r.interval, r.months) for r in gym] == [("monthly", (3_500,) * 3)]
    annual = [r for r in flow.recurring if "CLOUDNOTE" in r.merchant.upper()]
    assert [(r.interval, r.months) for r in annual] == [("annual", (0, 5_900, 0))]
    assert not [r for r in flow.recurring if "OLDBOX" in r.merchant.upper()]
    assert not [r for r in flow.recurring if "BOXCLUB" in r.merchant.upper()]
    groceries = flow.categories[("Food & Drink", "Groceries")]
    online = flow.categories[("Shopping", "Online")]  # not a subscription: spend
    assert groceries.method == "ets"
    assert online.method == "seasonal_naive"  # constant: nothing to model
    assert online.months[0] == Band(2_000, 2_000, 2_000)
    assert flow.categories[("Food & Drink", "Coffee")].method == "insufficient"
    for i, m in enumerate(flow.months):
        assert m.recurring == 3_500 + (5_900 if m.month == "2026-04" else 0)
        parts = [groceries.months[i], online.months[i]]
        assert m.total.point == m.recurring + sum(b.point for b in parts)
        assert m.total.lower == m.recurring + sum(b.lower for b in parts)
        assert m.total.upper == m.recurring + sum(b.upper for b in parts)


def _id(ids: dict[str, int], fragment: str) -> int:
    return next(i for name, i in ids.items() if fragment in name.upper())
