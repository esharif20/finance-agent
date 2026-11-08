"""ETS forecasting with prediction intervals and cash flow (#149, ADR 0015).
Synthetic data only."""

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from fractions import Fraction
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
    gbp_rows,
    project,
)
from finance_agent.engine.shortfall import outlook, pace
from finance_agent.ledger import Categorisation, Ledger, SplitPart, Transaction, fx
from synthetic_banks import revolut_pockets

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


def test_cash_flow_projects_recurring_income_with_its_paydays(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    # verifier #179: the what-if (#134 item 6, #151) needs money in as well as
    # money out, and the next payday's date
    rows: list[tuple[date, str, str]] = []
    for i in range(26):  # Jan 2024 to Feb 2026
        pay = "2100.00" if i < 20 else "2400.00"  # a pay rise in Sep 2025
        rows.append((_month(i, 25), "SYNTH ACME LTD SALARY", pay))
        if i < 15:  # a side job that stopped: lapsed, never projected
            rows.append((_month(i, 3), "SYNTH SIDEGIG PAYOUT", "150.00"))
        rows.append((_month(i, 9), "SYNTH GROCER", f"-{180 + i % 5}.00"))
    rows.append((date(2025, 12, 24), "SYNTH AUNT GIFT", "50.00"))  # one-off
    text = "".join(f"{d:%d/%m/%Y},{desc},{amt}\n" for d, desc, amt in rows)
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([write_export(text)])
        categorise.run(conn, 1)
        before = Ledger(conn, 1).transactions()
        flow = cash_flow(Ledger(conn, 1), date(2026, 3, 14), 3)
        assert Ledger(conn, 1).transactions() == before  # read-only

    salary = [r for r in flow.income if "ACME" in r.merchant.upper()]
    assert [(r.interval, r.months) for r in salary] == [("monthly", (240_000,) * 3)]
    assert salary[0].dates == (date(2026, 3, 25), date(2026, 4, 25), date(2026, 5, 25))
    assert [r.merchant for r in flow.income] == [salary[0].merchant]  # no others
    assert [m.income for m in flow.months] == [240_000] * 3
    assert all(type(m.income) is int and m.income > 0 for m in flow.months)
    assert not [r for r in flow.recurring if "ACME" in r.merchant.upper()]
    # money out is unchanged by income: the total is still spend only
    assert all(m.total.point > 0 for m in flow.months)


def test_a_short_seasonal_naive_band_is_not_one_lucky_error() -> None:
    # verifier #179: at 13 months there is one same-month error; when that one
    # matched exactly, the band must still reflect the month-to-month swings
    year = [1_000, 9_000] * 6
    f = forecast_category([*year, year[0]], 3)
    assert f.method == "seasonal_naive"
    assert all(b.lower < b.point < b.upper for b in f.months)


# --- FX (#227): a foreign row counts only through its #156 rate -------------

GBP_SPEND = st.lists(
    st.tuples(st.integers(0, 29), st.integers(1, 28), st.integers(1, 50_000)),
    max_size=40,
)


def _spend(i: int, on: date, pence: int, currency: str = "GBP") -> Transaction:
    c = Categorisation(
        "Spending", f"SYNTH M{i % 3}", ("Shopping", f"S{i % 2}"), "default", 1, i % 3
    )
    amount = Decimal(-pence).scaleb(-2)
    return Transaction(on, "SYNTH", amount, None, frozenset(), i, categorisation=c)


def _salary(i: int) -> Transaction:
    c = Categorisation("Income", None, None, "default", 1)
    on = _month(i, 25)
    return Transaction(
        on,
        "SYNTH ACME SALARY",
        Decimal(2100),
        None,
        frozenset(),
        500 + i,
        categorisation=c,
    )


def _what_if(txns: list[Transaction], as_of: date) -> object:
    flow = project(txns, {}, as_of)
    return flow, outlook(100_000, as_of, flow, pace(txns, flow, as_of), 5_000)


@settings(max_examples=60, deadline=None)
@given(GBP_SPEND, st.integers(1, 10**9), st.integers(0, 29), st.integers(1, 10**9))
def test_a_foreign_row_never_moves_a_gbp_forecast_without_a_rate(
    rows: list[tuple[int, int, int]], foreign: int, month: int, gbp: int
) -> None:
    """The forecast and the what-if alike (verifier #227)."""
    as_of = date(2026, 7, 14)
    txns = [_spend(i, _month(m, d), p) for i, (m, d, p) in enumerate(rows, start=1)]
    txns += [_salary(i) for i in range(30)]
    eur = replace(_spend(999, _month(month, 9), foreign), currency="EUR")
    base = _what_if(gbp_rows(txns, {}), as_of)
    assert _what_if(gbp_rows([*txns, eur], {}), as_of) == base
    assert _what_if(gbp_rows([*txns, eur], {999: None}), as_of) == base
    # with a rate it counts exactly as a GBP row of its converted amount
    converted = _what_if(gbp_rows([*txns, eur], {999: -gbp}), as_of)
    as_gbp = _spend(999, _month(month, 9), gbp)
    assert converted == _what_if([*txns, as_gbp], as_of)


def test_a_foreign_splits_parts_are_each_converted() -> None:
    parts = (SplitPart(-700, ("Shopping", "S0")), SplitPart(-300, ("Food", "F")))
    t = _spend(1, date(2026, 1, 3), 1_000)
    assert t.categorisation is not None
    split = replace(t.categorisation, parts=parts)
    t = replace(t, currency="EUR", categorisation=split)
    (out,) = gbp_rows([t], {1: -850})
    assert out.amount == Decimal("-8.50") and out.currency == "GBP"
    assert out.categorisation is not None
    assert out.categorisation.parts == (
        SplitPart(-595, ("Shopping", "S0")),
        SplitPart(-255, ("Food", "F")),
    )


MIXED = st.lists(
    st.tuples(
        st.integers(0, 29),  # months back
        st.integers(1, 28),  # day
        st.integers(1, 10**7),  # minor units spent, in the row's own currency
        st.sampled_from(["GBP", "EUR", "USD"]),
        st.one_of(st.none(), st.integers(1, 10**7)),  # GBP figure, None: no rate
        st.integers(0, 3),  # Split parts: 0 is no Split
    ),
    max_size=40,
)


@settings(max_examples=80, deadline=None)
@given(MIXED)
def test_verifier_mixed_currencies_match_an_exact_per_row_reference(
    rows: list[tuple[int, int, int, str, int | None, int]],
) -> None:
    """Verifier #227: over mixed currencies with a rate present or not, the
    forecast equals one built from GBP rows made by hand: each foreign row at
    its GBP figure, its Split parts adding up to exactly that, or dropped."""
    as_of = date(2026, 7, 14)
    txns: list[Transaction] = [_salary(i) for i in range(30)]
    gbp: dict[int, int | None] = {}
    reference: list[Transaction] = list(txns)
    for i, (m, d, own, cur, g, n) in enumerate(rows, start=1):
        t = _spend(i, _month(m, d), own)
        c = t.categorisation
        assert c is not None
        if n and own >= n:  # parts in its own currency, adding up to it
            cuts = [own // n] * (n - 1)
            cats = [("Shopping", f"P{k}") for k in range(n)]
            parts = tuple(
                SplitPart(-p, k)
                for p, k in zip([*cuts, own - sum(cuts)], cats, strict=True)
            )
            t = replace(t, categorisation=replace(c, parts=parts))
        t = replace(t, currency=cur)
        txns.append(t)
        if cur == "GBP":
            reference.append(t)
            continue
        gbp[i] = None if g is None else -g
        if g is None:
            continue  # fail closed: never counted as pence
        (out,) = gbp_rows([t], gbp)
        assert out.amount == Decimal(-g).scaleb(-2) and out.currency == "GBP"
        oc = out.categorisation
        assert oc is not None
        if t.categorisation and t.categorisation.parts:
            assert sum(p.amount_minor for p in oc.parts) == -g
            for p, q in zip(t.categorisation.parts, oc.parts, strict=True):
                assert p.category == q.category
                assert abs(Fraction(p.amount_minor * g, own) - q.amount_minor) <= 1
        reference.append(
            replace(t, amount=Decimal(-g).scaleb(-2), currency="GBP", categorisation=oc)
        )
    assert project(gbp_rows(txns, gbp), {}, as_of) == project(reference, {}, as_of)
    for out in gbp_rows(txns, gbp):
        assert out.currency == "GBP"
        assert out.amount == out.amount.quantize(Decimal("0.01"))


def test_cash_flow_converts_a_foreign_subscription_at_the_entered_rate(
    engine: Engine, tmp_path: Path
) -> None:
    """A EUR subscription is projected in GBP through the #156 rate, and left
    out (never counted as pence) while a rate is needed."""
    rows = [
        ("CARD_PAYMENT", "EUR", _month(i, 5), "SYNTH STREAMBOX", -1_299)
        for i in range(20, 26)
    ]
    p = tmp_path / "revolut.csv"
    p.write_text(revolut_pockets(rows))
    as_of = date(2026, 3, 14)
    with engine.begin() as conn:
        assert Ledger(conn, 1).import_files([p]).rejected == []
        categorise.run(conn, 1)
        assert cash_flow(Ledger(conn, 1), as_of).recurring == ()
        Ledger(conn, 2).set_rate("EUR", Decimal("100"), date(2020, 1, 1))
        assert cash_flow(Ledger(conn, 1), as_of).recurring == ()  # not user 2's
        Ledger(conn, 1).set_rate("EUR", Decimal("0.85"), date(2020, 1, 1))
        (sub,) = cash_flow(Ledger(conn, 1), as_of).recurring
    assert sub.months == (fx.convert(1_299, Decimal("0.85")),) * 3 == (1_104,) * 3
