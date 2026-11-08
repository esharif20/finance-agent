"""The monthly story's structured recap and its store (#266): Engine figures
in pence, causes by Merchant, recomputed on read, wording only while the
fingerprint holds, deleted with the month's Transactions. Synthetic only."""

import json
import random
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine, text

from finance_agent import categorise
from finance_agent.engine import story
from finance_agent.engine.detectors import Row
from finance_agent.ledger import Ledger
from finance_agent.ledger.stories import Stories
from finance_agent.storage import schema
from test_subscription_costs import hsbc

Export = Callable[[str], Path]
GROCERIES = ("Food & Drink", "Groceries")


def months_of(n: int, start: date = date(2026, 1, 1)) -> list[date]:
    out = [start]
    while len(out) < n:
        nxt = out[-1] + timedelta(days=32)
        out.append(nxt.replace(day=1))
    return out


def synthetic_rows() -> list[Row]:
    """Six steady months at two Merchants, then a July spike at one."""
    rows: list[Row] = []
    for m in months_of(7):
        rows.append(Row(len(rows) + 1, m.replace(day=3), "A MART", GROCERIES, -5000))
        rows.append(Row(len(rows) + 1, m.replace(day=9), "B FOODS", GROCERIES, -3000))
    rows.append(Row(len(rows) + 1, date(2026, 7, 20), "A MART", GROCERIES, -12000))
    return rows


def test_a_spike_names_its_category_and_the_merchant_behind_it() -> None:
    r = story.recap(synthetic_rows(), "2026-07")
    (c,) = r.changes
    assert (c.category, c.spent, c.expected, c.change) == (
        "Food & Drink / Groceries",
        20000,
        8000,
        12000,
    )
    assert [(d.merchant, d.spent, d.expected, d.change) for d in c.drivers] == [
        ("A MART", 17000, 5000, 12000)
    ]


def test_below_six_months_a_category_is_left_out_never_estimated() -> None:
    rows = [r for r in synthetic_rows() if r.posted_on >= date(2026, 2, 1)]
    assert story.recap(rows, "2026-07").changes == ()


def test_a_steady_month_has_no_changes() -> None:
    assert story.recap(synthetic_rows(), "2026-06").changes == ()


@pytest.mark.parametrize("month", ["2026-7", "2026-13", "2026-07-01", "x", ""])
def test_a_malformed_month_is_refused(month: str, engine: Engine) -> None:
    with pytest.raises(ValueError):
        story.bounds(month)
    with engine.begin() as conn, pytest.raises(ValueError):
        Stories(conn, 1).get(month)


@settings(max_examples=40, deadline=None)
@given(
    seed=st.integers(0, 2**32 - 1),
    later=st.lists(st.integers(-50_000, 50_000), max_size=5),
)
def test_order_and_later_rows_change_neither_recap_nor_fingerprint(
    seed: int, later: list[int]
) -> None:
    rows = synthetic_rows()
    shuffled = rows[:]
    random.Random(seed).shuffle(shuffled)  # noqa: S311 - test order only
    more = shuffled + [
        Row(100 + i, date(2026, 8, 1 + i), "A MART", GROCERIES, a)
        for i, a in enumerate(later)
    ]
    assert story.recap(more, "2026-07") == story.recap(rows, "2026-07")
    assert story.fingerprint(more, "2026-07") == story.fingerprint(rows, "2026-07")


@settings(max_examples=40, deadline=None)
@given(amount=st.integers(-50_000, 50_000).filter(bool), day=st.integers(1, 31))
def test_any_change_within_the_month_changes_the_fingerprint(
    amount: int, day: int
) -> None:
    rows = synthetic_rows()
    extra = Row(999, date(2026, 7, day), "B FOODS", GROCERIES, amount)
    assert story.fingerprint([*rows, extra], "2026-07") != story.fingerprint(
        rows, "2026-07"
    )


# --- through the Ledger -------------------------------------------------

STEADY = [
    row
    for m in months_of(7)
    for row in (
        (m.replace(day=3), "TESCO STORES 1234", "-50.00"),
        (m.replace(day=9), "SAINSBURYS S/MKTS", "-30.00"),
    )
]
SPIKE = [(date(2026, 7, 20), "TESCO STORES 1234", "-120.00")]


def seed(engine: Engine, export: Export, user_id: int = 1) -> None:
    with engine.begin() as conn:
        Ledger(conn, user_id).import_files([export(hsbc([*STEADY, *SPIKE]))])
        categorise.run(conn, user_id)


def test_figures_equal_the_ledgers_own_spend(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        s = story.story(conn, 1, "2026-07")
        (c,) = s.recap.changes
        top, sub = c.category.split(" / ")
        tree = {t.name: t for t in Ledger(conn, 1).spending_by_category("2026-07")}
        (leaf,) = [x for x in tree[top].children if x.name == sub]
        assert c.spent == int(leaf.amount.scaleb(2)) == 20000
        assert [d.spent for d in c.drivers] == [
            int(m.amount.scaleb(2))
            for m in leaf.children
            if m.name == c.drivers[0].merchant
        ]


def test_wording_is_reused_only_while_the_fingerprint_holds(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        built = story.story(conn, 1, "2026-07")
        assert built.wording is None
        assert story.save(conn, 1, built, "July: groceries were up.")
        again = story.story(conn, 1, "2026-07")
        assert again.wording == "July: groceries were up."
        # an unchanged month: the recomputation equals the stored figures
        stored = Stories(conn, 1).get("2026-07")
        assert stored is not None and stored.recap == json.loads(
            json.dumps(again.recap.to_json())
        )
        Ledger(conn, 1).import_files(
            [write_export(hsbc([(date(2026, 7, 25), "TESCO STORES 1234", "-9.00")]))]
        )
        categorise.run(conn, 1)
        fresh = story.story(conn, 1, "2026-07")
        assert fresh.wording is None  # a stale wording is never served
        assert fresh.recap.changes[0].spent == 20900
        # wording grounded on the old figures is refused
        assert not story.save(conn, 1, built, "stale wording")
        assert story.save(conn, 1, fresh, None)


def drop(conn: Connection, where: str) -> None:
    """Delete Transactions as an Import or Account deletion does: what points
    at them first, then the rows."""
    ids = f'SELECT id FROM "transaction" WHERE {where}'  # noqa: S608 - test SQL
    for t in schema.metadata.sorted_tables:
        for fk in t.foreign_keys:
            if fk.column.table is schema.transaction:
                stmt = f"DELETE FROM {t.name} WHERE {fk.parent.name} IN ({ids})"  # noqa: S608
                conn.execute(text(stmt))
    conn.execute(text(f'DELETE FROM "transaction" WHERE {where}'))  # noqa: S608


def test_a_deleted_transaction_takes_its_months_story_only(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        for month in ("2026-06", "2026-07"):
            assert story.save(conn, 1, story.story(conn, 1, month), month)
        drop(conn, "posted_on = '2026-07-20'")
        assert Stories(conn, 1).months() == ["2026-06"]


def test_stories_are_scoped_to_their_user(engine: Engine, write_export: Export) -> None:
    seed(engine, write_export, user_id=1)
    seed(engine, write_export, user_id=2)
    with engine.begin() as conn:
        assert story.save(conn, 1, story.story(conn, 1, "2026-07"), "one's")
        assert Stories(conn, 2).get("2026-07") is None
        assert story.story(conn, 2, "2026-07").wording is None
        # user 2 deleting their July row leaves user 1's story
        drop(conn, "user_id = 2 AND posted_on = '2026-07-20'")
        assert Stories(conn, 2).forget() == 0
        assert Stories(conn, 1).months() == ["2026-07"]
        assert Stories(conn, 1).forget() == 1


def test_the_evaluation_reproduces() -> None:
    import gate
    import story as harness

    fresh = json.loads(json.dumps(harness.evaluate()))
    assert gate.differences(fresh, json.loads(harness.RESULTS.read_text()), 0) == []
    m = fresh["models"]["candidate"]["metrics"]
    base = fresh["models"]["baseline"]["metrics"]
    assert m["figures_exact"] == 1.0
    assert m["cause.recall"] > base["cause.recall"]
    assert m["coverage"] > base["coverage"]


def test_migration_0029_round_trips_and_its_trigger_deletes_by_month(
    tmp_path: Path,
) -> None:
    from test_storage import round_trip

    cols = "user_id, month, fingerprint, recap, created_at"
    rows = tuple(
        f"INSERT INTO monthly_story ({cols}) VALUES (1, '{m}', 'f', '{{}}', 'x')"  # noqa: S608
        for m in ("2026-01", "2026-02")
    )
    eng = round_trip(tmp_path, "0028", "0029", {"monthly_story"}, rows)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM note"))
        conn.execute(text('DELETE FROM "transaction" WHERE id = 9'))  # 2026-01-02
        left = conn.execute(text("SELECT month FROM monthly_story")).scalars().all()
        assert left == ["2026-02"]
    eng.dispose()
