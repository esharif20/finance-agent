"""Subscription status, monthly cost and totals (#105). Synthetic data only."""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from random import Random

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.categorise.subscriptions import (
    Charge,
    CostSummary,
    costs,
    display,
    subscription_costs,
)
from finance_agent.engine.forecast import cash_flow
from finance_agent.ledger import Ledger, SubscriptionEntry, UnknownTarget
from synthetic_banks import revolut_pockets

Export = Callable[[str], Path]


def hsbc(rows: list[tuple[date, str, str]]) -> str:
    return "".join(f"{d:%d/%m/%Y},{desc},{amt}\n" for d, desc, amt in rows)


TODAY = date(2026, 6, 15)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def entry(
    mid: int,
    kind: str = "subscription",
    group: str = "Streaming",
    status: str = "active",
    interval: str | None = "monthly",
    source: str = "detection",
) -> SubscriptionEntry:
    return SubscriptionEntry(
        mid, f"SYNTH {mid}", kind, group, status, "pattern", source,
        date(2026, 1, 1), 3, interval, T0, T0,
    )  # fmt: skip


def test_monthly_cost_per_interval_is_exact_and_rounds_once() -> None:
    s = subscription_costs(
        [entry(1, interval="weekly"), entry(2), entry(3, interval="annual")],
        [
            Charge(1, date(2026, 6, 12), 999),
            Charge(2, date(2026, 6, 1), 1299),
            Charge(3, date(2026, 1, 20), 9999),
        ],
        TODAY,
    )
    by = {c.entry.merchant_id: c for c in s.items}
    assert by[1].monthly == Fraction(999 * 52, 12)  # 4329 exactly
    assert by[2].monthly == 1299
    assert by[3].monthly == Fraction(9999, 12)  # 833.25p
    assert display(Fraction(9999, 12)) == 833
    # the total is the unrounded sum, rounded once: 4329 + 1299 + 833.25
    assert display(s.kind_totals["subscription"]) == 6461


def test_the_total_rounds_the_exact_sum_not_the_rounded_parts() -> None:
    s = subscription_costs(
        [entry(1, interval="annual"), entry(2, interval="annual")],
        [Charge(1, date(2026, 6, 1), 1002), Charge(2, date(2026, 6, 1), 1002)],
        TODAY,
    )
    assert [display(c.monthly) for c in s.items if c.monthly] == [84, 84]  # 83.5 each
    assert display(s.kind_totals["subscription"]) == 167  # not 168


def test_half_a_penny_rounds_half_even() -> None:
    assert display(Fraction(1, 2)) == 0
    assert display(Fraction(3, 2)) == 2
    assert display(Fraction(5, 2)) == 2


def test_the_latest_charge_sets_the_price() -> None:
    (c,) = subscription_costs(
        [entry(1)],
        [Charge(1, date(2026, 5, 1), 1000), Charge(1, date(2026, 6, 1), 1200)],
        TODAY,
    ).items
    assert (c.monthly, c.last_charge_on) == (1200, date(2026, 6, 1))


def status_on(today: date, interval: str, last: date) -> str:
    (c,) = subscription_costs(
        [entry(1, interval=interval)], [Charge(1, last, 500)], today
    ).items
    return c.status


def test_status_follows_the_grace_periods() -> None:
    # monthly: due 1 Jun, grace 5 days, second charge due 1 Jul
    last = date(2026, 5, 1)
    assert status_on(date(2026, 6, 6), "monthly", last) == "active"
    assert status_on(date(2026, 6, 7), "monthly", last) == "overdue"
    assert status_on(date(2026, 7, 6), "monthly", last) == "overdue"
    assert status_on(date(2026, 7, 7), "monthly", last) == "lapsed"
    # weekly: due 8 Jun, grace 2 days
    w = date(2026, 6, 1)
    assert status_on(date(2026, 6, 10), "weekly", w) == "active"
    assert status_on(date(2026, 6, 11), "weekly", w) == "overdue"
    assert status_on(date(2026, 6, 18), "weekly", w) == "lapsed"
    # annual: due 1 Jan 2027, grace 15 days
    a = date(2026, 1, 1)
    assert status_on(date(2027, 1, 16), "annual", a) == "active"
    assert status_on(date(2027, 1, 17), "annual", a) == "overdue"
    assert status_on(date(2028, 1, 17), "annual", a) == "lapsed"


def test_month_end_charges_are_due_at_the_next_month_end() -> None:
    # 31 Jan -> due 28 Feb (2026 is not a leap year), overdue after 5 Mar
    assert status_on(date(2026, 3, 5), "monthly", date(2026, 1, 31)) == "active"
    assert status_on(date(2026, 3, 6), "monthly", date(2026, 1, 31)) == "overdue"


def test_lapsed_cancelled_and_suggested_never_count() -> None:
    s = subscription_costs(
        [
            entry(1),
            entry(2, status="user_cancelled", source="user"),
            entry(3, status="suggested"),
            entry(4),  # lapsed: last charged in March
        ],
        [Charge(m, date(2026, 6, 1), 1000) for m in (1, 2, 3)]
        + [Charge(4, date(2026, 3, 1), 1000)],
        TODAY,
    )
    status = {c.entry.merchant_id: (c.status, c.counted) for c in s.items}
    assert status == {
        1: ("active", True),
        2: ("cancelled", False),
        3: ("suggested", False),
        4: ("lapsed", False),
    }
    assert s.kind_totals["subscription"] == 1000


def test_a_foreign_currency_charge_needs_a_rate_and_is_never_guessed() -> None:
    s = subscription_costs(
        [entry(1), entry(2)],
        [Charge(1, date(2026, 6, 1), 1000, "USD"), Charge(2, date(2026, 6, 1), 700)],
        TODAY,
    )
    by = {c.entry.merchant_id: c for c in s.items}
    assert (by[1].monthly, by[1].cost_note, by[1].counted) == (
        None,
        "rate_needed",
        False,
    )
    assert s.kind_totals["subscription"] == 700
    assert s.rate_needed == 1


def test_no_charge_or_no_interval_gives_no_cost() -> None:
    s = subscription_costs(
        [entry(1, interval=None), entry(2), entry(3, interval=None)],
        [
            Charge(1, date(2026, 6, 1), 1000),
            # two charges a month apart: the interval is read from them
            Charge(3, date(2026, 5, 1), 400),
            Charge(3, date(2026, 6, 1), 400),
        ],
        TODAY,
    )
    by = {c.entry.merchant_id: c for c in s.items}
    assert (by[1].status, by[1].monthly, by[1].cost_note) == (
        "active",
        None,
        "interval_needed",
    )
    assert (by[2].status, by[2].monthly, by[2].cost_note) == (
        "active",
        None,
        "no_charge",
    )
    assert (by[3].interval, by[3].monthly) == ("monthly", 400)


def test_negative_entries_are_not_listed() -> None:
    neg = SubscriptionEntry(
        1, "SYNTH 1", None, None, "not_subscription", None, "user",
        date(2026, 1, 1), None, None, T0, T0,
    )  # fmt: skip
    s = subscription_costs([neg], [Charge(1, date(2026, 6, 1), 1000)], TODAY)
    assert s.items == [] and s.kind_totals == {}


# --- properties ---------------------------------------------------------------

STATUSES = ("active", "suggested", "user_cancelled")
GROUPS = (("subscription", "Streaming"), ("subscription", "Software"),
          ("bill", "Energy"))  # fmt: skip
Rows = tuple[list[SubscriptionEntry], list[Charge]]


@st.composite
def registry_and_charges(draw: st.DrawFn) -> Rows:
    es: list[SubscriptionEntry] = []
    cs: list[Charge] = []
    for mid in range(1, draw(st.integers(0, 8)) + 1):
        kind, group = draw(st.sampled_from(GROUPS))
        status = draw(st.sampled_from(STATUSES))
        interval = draw(st.sampled_from(("weekly", "monthly", "annual", None)))
        es.append(entry(mid, kind, group, status, interval))
        for _ in range(draw(st.integers(0, 4))):
            on = draw(st.dates(date(2024, 1, 1), TODAY))
            cur = draw(st.sampled_from(("GBP", "GBP", "EUR")))
            cs.append(Charge(mid, on, draw(st.integers(1, 10**7)), cur))
    return es, cs


@given(registry_and_charges())
def test_totals_are_the_sums_of_the_unrounded_counted_parts(rows: Rows) -> None:
    s = subscription_costs(*rows, TODAY)
    for kind in ("subscription", "bill"):
        parts = [
            c.monthly
            for c in s.items
            if c.counted and c.monthly is not None and c.entry.kind == kind
        ]
        assert s.kind_totals.get(kind, 0) == sum(parts, Fraction(0))
        in_groups = [v for (k, _), v in s.group_totals.items() if k == kind]
        assert sum(in_groups, Fraction(0)) == s.kind_totals.get(kind, 0)
    for c in s.items:
        priced = c.monthly is not None
        assert c.counted == (c.status in ("active", "overdue") and priced)


@given(registry_and_charges())
def test_user_decisions_are_never_overridden(rows: Rows) -> None:
    s = subscription_costs(*rows, TODAY)
    for c in s.items:
        assert (c.entry.status == "user_cancelled") == (c.status == "cancelled")
        if c.status in ("cancelled", "lapsed", "suggested"):
            assert not c.counted


@given(registry_and_charges(), st.randoms(use_true_random=False))
def test_row_order_does_not_change_the_result(rows: Rows, rnd: Random) -> None:
    es, cs = list(rows[0]), list(rows[1])
    before = subscription_costs(es, cs, TODAY)
    rnd.shuffle(es)
    rnd.shuffle(cs)
    assert subscription_costs(es, cs, TODAY) == before


# --- through the Ledger ---------------------------------------------------------


def import_and_cost(
    engine: Engine, export: Export, rows: list[tuple[date, str, str]]
) -> CostSummary:
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([export(hsbc(rows))])
        categorise.run(conn, 1)
        return costs(Ledger(conn, 1), TODAY)


def test_costs_read_the_registry_and_the_merchants_charges(
    engine: Engine, write_export: Export
) -> None:
    rows = [(date(2026, m, 3), "NETFLIX.COM", "-10.99") for m in (4, 5, 6)]
    rows += [(date(2026, 6, 4), "SYNTH CAFE", "-3.20")]
    (c,) = import_and_cost(engine, write_export, rows).items
    assert (c.entry.merchant, c.status, c.interval, c.monthly) == (
        "NETFLIX.COM", "active", "monthly", 1099,
    )  # fmt: skip


def test_cancel_is_a_user_decision_detection_never_undoes(
    engine: Engine, write_export: Export
) -> None:
    rows = [(date(2026, m, 3), "NETFLIX.COM", "-10.99") for m in (4, 5, 6)]
    import_and_cost(engine, write_export, rows)
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        (e,) = led.subscriptions()
        led.cancel_subscription(e.merchant_id)
        categorise.run(conn, 1)  # recognition runs again
        (after,) = led.subscriptions()
        s = costs(led, TODAY)
    assert (after.status, after.source, after.group, after.reason) == (
        "user_cancelled", "user", "Streaming", "known_service",
    )  # fmt: skip
    assert s.items[0].status == "cancelled" and s.kind_totals == {}


def test_cancelling_an_unlisted_merchant_is_refused(
    ledger_for: Callable[[int], Ledger],
) -> None:
    with pytest.raises(UnknownTarget):
        ledger_for(1).cancel_subscription(999)


def test_a_foreign_charge_costs_in_gbp_once_a_rate_exists(
    engine: Engine, tmp_path: Path
) -> None:
    eur = [
        ("CARD_PAYMENT", "EUR", date(2026, m, 3), "SPOTIFY", -1150) for m in (4, 5, 6)
    ]
    p = tmp_path / "revolut.csv"
    p.write_text(revolut_pockets(eur))
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        assert led.import_files([p]).rejected == []
        categorise.run(conn, 1)
        s = costs(led, TODAY)
        (c,) = s.items
        assert (c.monthly, c.cost_note, c.counted, s.rate_needed) == (
            None, "rate_needed", False, 1,
        )  # fmt: skip
        led.set_rate("EUR", Decimal("0.8"), date(2026, 1, 1))
        s = costs(led, TODAY)
    assert (s.items[0].monthly, s.items[0].cost_note) == (920, None)  # 11.50 EUR
    assert s.kind_totals == {"subscription": 920} and s.rate_needed == 0


def test_a_cancel_survives_new_charges_and_confirm_brings_it_back(
    engine: Engine, write_export: Export
) -> None:
    # verifier #185: a cancel racing a re-detection with new evidence
    rows = [(date(2026, m, 3), "NETFLIX.COM", "-10.99") for m in (2, 3, 4)]
    import_and_cost(engine, write_export, rows)
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        (e,) = led.subscriptions()
        led.cancel_subscription(e.merchant_id)
        more = [(date(2026, m, 3), "NETFLIX.COM", "-12.99") for m in (5, 6)]
        led.import_files([write_export(hsbc(more))])
        categorise.run(conn, 1)  # new evidence: detection still loses
        (c,) = costs(led, TODAY).items
        assert (c.status, c.counted, c.monthly) == ("cancelled", False, 1299)
        led.confirm_subscription(e.merchant_id)
        categorise.run(conn, 1)
        s = costs(led, TODAY)
    assert s.items[0].status == "active" and s.kind_totals == {"subscription": 1299}


def test_the_forecast_stops_projecting_a_cancelled_subscription(
    engine: Engine, write_export: Export
) -> None:
    rows = [(date(2026, m, 3), "NETFLIX.COM", "-10.99") for m in range(1, 7)]
    import_and_cost(engine, write_export, rows)
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        (e,) = led.subscriptions()
        projected = cash_flow(led, date(2026, 7, 10), 3)
        led.cancel_subscription(e.merchant_id)
        cancelled = cash_flow(led, date(2026, 7, 10), 3)
    assert [r.months for r in projected.recurring] == [(1099,) * 3]
    assert not cancelled.recurring
    assert [m.recurring for m in cancelled.months] == [0] * 3
    assert not cancelled.categories  # its charges are not discretionary spend


INTERVAL_FACTOR = {
    "weekly": Fraction(52, 12),
    "monthly": Fraction(1),
    "annual": Fraction(1, 12),
}


@given(
    st.lists(
        st.tuples(st.integers(1, 10**15), st.sampled_from(list(INTERVAL_FACTOR))),
        min_size=1,
        max_size=30,
    )
)
def test_extreme_amounts_total_exactly_and_round_once(
    parts: list[tuple[int, str]],
) -> None:
    # verifier #185: huge prices, every interval, one group
    es = [entry(i, interval=iv) for i, (_, iv) in enumerate(parts)]
    cs = [Charge(i, date(2026, 6, 10), p) for i, (p, _) in enumerate(parts)]
    s = subscription_costs(es, cs, TODAY)
    exact = sum((p * INTERVAL_FACTOR[iv] for p, iv in parts), Fraction(0))
    assert s.kind_totals == {"subscription": exact}
    assert s.group_totals == {("subscription", "Streaming"): exact}
    shown = display(exact)
    assert type(shown) is int and abs(shown - exact) <= Fraction(1, 2)
    if exact - int(exact) == Fraction(1, 2):
        assert shown % 2 == 0  # half-even
