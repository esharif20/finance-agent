"""The Insight pipeline and store (#150): detectors over Ledger reads, stored
idempotently, ranked by impact x confidence x relevance and filtered by
constraint Memories. Synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine

from finance_agent import categorise
from finance_agent.engine import insights as ins
from finance_agent.engine.detectors import Row
from finance_agent.engine.ranking import Relevance
from finance_agent.ledger import Ledger
from finance_agent.ledger.insights import Found, Insights
from finance_agent.ledger.proposals import (
    MemoryDraft,
    MemoryType,
    Proposals,
    Provenance,
)
from test_fx import CAFE, EXCHANGE, load
from test_subscription_costs import hsbc

Export = Callable[[str], Path]


def test_rows_are_gbp_pence_and_a_foreign_row_needing_a_rate_is_left_out(
    engine: Engine, tmp_path: Path
) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        load(led, tmp_path, [CAFE])
        categorise.run(conn, 1)
        assert ins.rows(led) == []  # rate needed: never guessed
        load(led, tmp_path, EXCHANGE)  # the user's own exchange gives the rate
        categorise.run(conn, 1)
        assert [r.amount for r in ins.rows(led)] == [-2000]  # transfers never count


# a monthly subscription that steps up in June, and a café
ROWS = [
    *((date(2026, m, 3), "SPOTIFY", "-10.99") for m in (1, 2, 3, 4, 5)),
    (date(2026, 6, 3), "SPOTIFY", "-12.99"),
    (date(2026, 6, 4), "SYNTH CAFE", "-3.20"),
]


def seed(engine: Engine, export: Export, user_id: int = 1) -> None:
    with engine.begin() as conn:
        Ledger(conn, user_id).import_files([export(hsbc(ROWS))])
        categorise.run(conn, user_id)


def kinds(conn: Connection, user_id: int = 1) -> list[tuple[str, str]]:
    return [(i.type, i.key) for i in Insights(conn, user_id).current()]


def test_refresh_stores_what_the_detectors_raise_once(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        ins.refresh(conn, 1)
        first = Insights(conn, 1).current()
        ins.refresh(conn, 1)  # idempotent: same rows, same ids
        assert Insights(conn, 1).current() == first
    (rise,) = [i for i in first if i.type == "price_increase"]
    assert (rise.key, rise.category, rise.raised_on) == (
        "Spotify",
        "Subscriptions / Streaming",
        date(2026, 6, 3),
    )
    assert rise.impact == (1299 - 1099) * 12 and len(rise.evidence) == 2
    assert ins.explain(rise) == "Spotify went up: about £24.00 more a year."


def test_a_cancelled_merchant_gets_no_price_rise_and_its_rating_survives(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        led, store = Ledger(conn, 1), Insights(conn, 1)
        ins.refresh(conn, 1)
        (rise,) = [i for i in store.current() if i.type == "price_increase"]
        store.give(rise.id, "down")
        (e,) = led.subscriptions()
        led.cancel_subscription(e.merchant_id)
        ins.refresh(conn, 1)
        assert ("price_increase", "Spotify") not in kinds(conn)
        # the Insight is gone; its rating still teaches relevance
        assert store.signals() == [
            ("price_increase", "Subscriptions / Streaming", False)
        ]


def test_signals_are_explicit_and_a_dismissal_hides_the_insight(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        ins.refresh(conn, 1)
        store = Insights(conn, 1)
        assert store.signals() == []  # being listed teaches nothing
        a, *_ = store.current()
        assert store.give(a.id, "up").signal == "up"
        assert store.give(a.id, "down").signal == "down"  # the latest replaces it
        assert store.signals() == [(a.type, a.category, False)]
        store.give(a.id, "dismissed")
        ins.refresh(conn, 1)
        assert a.id not in [i.id for i in store.current()]
        assert store.signals() == [(a.type, a.category, False)]


def test_ranked_applies_relevance_then_constraint_memories(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        ins.refresh(conn, 1)
        got = ins.ranked(conn, 1)
        assert [r.score for r in got] == sorted((r.score for r in got), reverse=True)
        assert all(r.relevance == Relevance() for r in got)
        Insights(conn, 1).give(got[0].insight.id, "up")
        assert ins.ranked(conn, 1)[0].relevance == Relevance(1, 0)

        props = Proposals(conn, 1)
        said = Provenance(turn_id="t1", quote="synthetic", said_on=date(2026, 6, 5))
        memories: tuple[tuple[MemoryType, str], ...] = (
            ("preference", "spotify"),
            ("constraint", "streaming"),
        )
        for kind, tag in memories:
            p = props.propose_memory(
                MemoryDraft(type=kind, text="synthetic", tags=(tag,), provenance=said)
            )
            props.confirm(p.id)
        left = [r.insight.key for r in ins.ranked(conn, 1)]
        assert "Spotify" not in left  # the constraint, not the preference
        assert len(Insights(conn, 1).current()) > len(left)  # filtered, not deleted


def test_another_users_insights_are_neither_listed_nor_rated(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export, user_id=2)
    with engine.begin() as conn:
        ins.refresh(conn, 2)
        theirs = Insights(conn, 2).current()
        assert theirs and Insights(conn, 1).current() == []
        assert ins.ranked(conn, 1) == []


# --- pure: the window, renewals not yet due and the data gates -------------------

SUBS = ("Subscriptions", "Streaming")


def row(i: int, on: date, merchant: str, pence: int, cat: tuple[str, str]) -> Row:
    return Row(i, on, merchant, cat, -pence)


def test_a_renewal_dated_after_the_last_row_waits() -> None:
    fee = row(1, date(2025, 7, 1), "SYNTH ANNUAL", 5000, SUBS)
    due_flag = date(2026, 7, 1) - timedelta(days=28)
    early = [fee, row(2, due_flag - timedelta(days=1), "SYNTH CAFE", 300, SUBS)]
    assert [f.type for f in ins.found(early)] == []
    later = [fee, row(2, due_flag, "SYNTH CAFE", 300, SUBS)]
    assert [(f.type, f.key, f.raised_on) for f in ins.found(later)] == [
        ("upcoming_renewal", "SYNTH ANNUAL", due_flag)
    ]


def test_only_the_window_before_the_last_row_is_kept() -> None:
    old = [row(i, date(2025, 1, 1 + i), "SYNTH ANNUAL", 5000, SUBS) for i in (1,)]
    recent = row(9, date(2026, 6, 1), "SYNTH CAFE", 300, ("Food & Drink", "Coffee"))
    assert ins.found([*old, recent]) == []  # its renewal was raised in 2025


CATS = (("Food & Drink", "Groceries"), ("Shopping", "Clothes"), ("Bills", "Energy"))


@st.composite
def thin_histories(draw: st.DrawFn) -> list[Row]:
    """Under every gate: each Merchant charged at most twice, under five months
    of history, nothing under Subscriptions."""
    start = date(2026, 1, 1)
    out: list[Row] = []
    for m in range(draw(st.integers(0, 6))):
        cat = draw(st.sampled_from(CATS))
        for _ in range(draw(st.integers(1, 2))):
            out.append(
                Row(
                    len(out) + 1,
                    start + timedelta(days=draw(st.integers(0, 140))),
                    f"SYNTH {m}",
                    cat,
                    draw(st.integers(-50_000, 5_000)),
                )
            )
    return out


@given(thin_histories(), st.booleans())
def test_insufficient_data_never_yields_an_insight(rs: list[Row], stop: bool) -> None:
    assert ins.found(rs, {r.merchant for r in rs} if stop else ()) == []


def test_found_carries_the_category_of_its_latest_evidence() -> None:
    rs = [
        row(i, date(2026, i, 3), "SYNTH GYM", 3000, ("Health", "Gym"))
        for i in (1, 2, 3)
    ]
    rs.append(row(4, date(2026, 4, 3), "SYNTH GYM", 3000, ("Health", "Fitness")))
    (rec,) = ins.found(rs)
    assert rec == Found(
        "recurring", "SYNTH GYM", "Health / Fitness", date(2026, 4, 3),
        3000 * 12, 90, (1, 2, 3, 4),
    )  # fmt: skip


def test_a_refresh_with_nothing_new_writes_nothing(
    engine: Engine, write_export: Export
) -> None:
    """Verifier (#150): GET /api/insights refreshes on every read, so an
    unchanged Ledger must not take SQLite's write lock (two concurrent reads
    would otherwise race to write and one fail)."""
    from sqlalchemy import event

    seed(engine, write_export)
    with engine.begin() as conn:
        ins.refresh(conn, 1)
        assert Insights(conn, 1).current()
    writes: list[str] = []

    def spy(*args: object) -> None:
        sql = str(args[2]).lstrip().upper()
        if sql.startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(sql.split()[0])

    event.listen(engine, "before_cursor_execute", spy)
    try:
        with engine.begin() as conn:
            ins.refresh(conn, 1)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert writes == []
