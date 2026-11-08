"""The weekly briefing and its regret review (#152). Synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.engine import briefing, forecast
from finance_agent.engine import insights as ins
from finance_agent.ledger import Categorisation, Ledger, Transaction
from test_insights import ROWS, seed
from test_subscription_costs import hsbc

Export = Callable[[str], Path]

WEEK = date(2026, 6, 15)  # a Monday


def txn(i: int, on: date, kind: str, merchant_id: int | None = None) -> Transaction:
    cat = Categorisation(
        kind, "SYNTH", ("Shopping", "Online"), "default", 1, merchant_id
    )
    return Transaction(on, f"SYNTH {i}", Decimal("-5.00"), None, frozenset(), i,
                       categorisation=cat)  # fmt: skip


KINDS = ("Spending", "Income", "Transfer", "Refund", "Fee")
txns = st.lists(
    st.builds(
        txn,
        st.integers(1, 10_000),
        st.dates(WEEK - timedelta(days=40), WEEK + timedelta(days=10)),
        st.sampled_from(KINDS),
        st.none() | st.integers(1, 5),
    ),
    unique_by=lambda t: t.id,
    max_size=40,
)


def test_week_of_is_its_monday() -> None:
    assert briefing.week_of(date(2026, 6, 21)) == WEEK  # a Sunday
    assert briefing.week_of(WEEK) == WEEK


@given(txns, st.sets(st.integers(1, 5)))
def test_picks_are_recent_discretionary_spending_only(
    ts: list[Transaction], recurring: set[int]
) -> None:
    picks = briefing.pick(1, WEEK, ts, {}, recurring)
    assert len(picks) <= briefing.PICKS
    for t in picks:
        c = t.categorisation
        assert c is not None and c.kind == "Spending"  # never Income or a Transfer
        assert c.merchant_id not in recurring
        assert WEEK - timedelta(days=briefing.LOOKBACK_DAYS) <= t.posted_on < WEEK
    eligible = [
        t for t in ts
        if t.categorisation and t.categorisation.kind == "Spending"
        and t.categorisation.merchant_id not in recurring
        and WEEK - timedelta(days=briefing.LOOKBACK_DAYS) <= t.posted_on < WEEK
    ]  # fmt: skip
    assert len(picks) == min(briefing.PICKS, len(eligible))


@given(txns, st.randoms(use_true_random=False))
def test_picks_are_stable_under_row_order_and_answers_this_week(
    ts: list[Transaction], rnd: object
) -> None:
    first = briefing.pick(1, WEEK, ts, {}, set())
    shuffled = list(ts)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    assert briefing.pick(1, WEEK, shuffled, {}, set()) == first
    # answering this week's picks leaves the week's list as it was
    answered = {t.id: WEEK for t in first}
    assert briefing.pick(1, WEEK, ts, answered, set()) == first


@given(txns, st.data())
def test_an_item_answered_in_another_week_is_never_picked_again(
    ts: list[Transaction], data: st.DataObject
) -> None:
    ids = [t.id for t in ts]
    answered = data.draw(st.dictionaries(
        st.sampled_from(ids) if ids else st.nothing(),
        st.sampled_from((WEEK - timedelta(weeks=1), WEEK + timedelta(weeks=2))),
    ))  # fmt: skip
    picks = briefing.pick(1, WEEK, ts, answered, set())
    assert not {t.id for t in picks} & answered.keys()


def test_the_seed_is_the_user_and_the_week() -> None:
    ts = [txn(i, WEEK - timedelta(days=1 + i % 20), "Spending") for i in range(1, 30)]
    a = briefing.pick(1, WEEK, ts, {}, set())
    assert len(a) == briefing.PICKS
    # another user's seed picks another handful, not the earliest rows
    assert briefing.pick(2, WEEK, ts, {}, set()) != a


# --- the briefing over a Ledger ---------------------------------------------


def test_an_empty_ledger_says_not_enough_history_and_shows_no_figures(
    engine: Engine,
) -> None:
    with engine.begin() as conn:
        b = briefing.build(conn, 1, WEEK)
    assert b.week == WEEK
    assert b.insufficient == ("insights", "forecast")
    assert b.forecast is None and b.insights == [] and b.renewals == []
    assert b.regret == [] and b.next_payday is None


def test_a_short_history_briefs_insights_and_regret_but_no_forecast(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)  # Jan-Jun 2026: under the forecast's 12 months
    with engine.begin() as conn:
        b = briefing.build(conn, 1, WEEK)
        feed = ins.ranked(conn, 1)
    assert b.insufficient == ("forecast",) and b.forecast is None
    assert [r.insight.id for r in b.insights] == [
        r.insight.id for r in feed[: briefing.TOP_INSIGHTS]
    ]
    # the café is discretionary; the Spotify subscription never is
    assert [t.description for t in b.regret] == ["SYNTH CAFE"]


# fourteen months of irregular grocery shopping, and a salary on the 25th
GROCERIES = [
    row
    for m in range(14)
    for on in (date(2025, 4, 1) + timedelta(days=31 * m),)
    for row in (
        (on.replace(day=5), "TESCO STORES", f"-{20 + 7 * (m % 5)}.{m:02d}"),
        (on.replace(day=19), "TESCO STORES", f"-{45 - 3 * (m % 4)}.50"),
        (on.replace(day=25), "SALARY SYNTH LTD", "2000.00"),
    )
]


def test_the_forecast_band_is_the_cash_flow_forecast_for_the_month(
    engine: Engine, write_export: Export
) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        led.import_files([write_export(hsbc(GROCERIES))])
        categorise.run(conn, 1)
        b = briefing.build(conn, 1, WEEK)
        (month,) = forecast.cash_flow(led, WEEK, 1).months
    assert "forecast" not in b.insufficient and b.forecast is not None
    assert (b.forecast.month, b.forecast.recurring) == ("2026-06", month.recurring)
    assert (b.forecast.point, b.forecast.lower, b.forecast.upper) == (
        month.total.point,
        month.total.lower,
        month.total.upper,
    )
    assert b.forecast.lower <= b.forecast.point <= b.forecast.upper
    assert b.next_payday == date(2026, 6, 25)
    # one shop in the four weeks before; the salary is never asked about
    assert [(t.description, t.posted_on) for t in b.regret] == [
        ("TESCO STORES", date(2026, 5, 19))
    ]


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.dates(date(2026, 1, 1), date(2026, 9, 30)))
def test_the_briefing_is_deterministic_for_a_ledger_and_week(
    engine: Engine, write_export: Export, day: date
) -> None:
    with engine.begin() as conn:
        if not Ledger(conn, 1).transactions():
            Ledger(conn, 1).import_files([write_export(hsbc(ROWS))])
            categorise.run(conn, 1)
        week = briefing.week_of(day)
        assert briefing.build(conn, 1, week) == briefing.build(conn, 1, day)


def test_an_annual_subscription_due_soon_is_an_upcoming_renewal(
    engine: Engine, write_export: Export
) -> None:
    rows = [
        (date(2025, 7, 1), "SPOTIFY", "-99.00"),
        (date(2026, 6, 10), "SYNTH CAFE", "-3.20"),
    ]
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([write_export(hsbc(rows))])
        categorise.run(conn, 1)
        b = briefing.build(conn, 1, WEEK)
        assert [(r.merchant, r.due_on, r.amount) for r in b.renewals] == [
            ("Spotify", date(2026, 7, 1), 9900)
        ]
        # by August it is past, so no longer upcoming
        assert briefing.build(conn, 1, date(2026, 8, 3)).renewals == []
