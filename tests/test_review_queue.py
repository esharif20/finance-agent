"""The Review Queue (#110): typed, user-scoped items, ordered by Merchant
frequency; answers become Corrections or registry decisions, and undo reverts."""

import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.ledger import Ledger, UnknownTarget
from finance_agent.ledger.review import (
    Answer,
    InvalidAnswer,
    Item,
    ReviewQueue,
    StaleItem,
    by_frequency,
    order,
)
from finance_agent.storage import open_db, schema
from synthetic_lloyds import History, Txn, render_export

START = date(2026, 3, 1)
# synthetic descriptors no rule knows, so each is an Uncategorised Merchant
UNKNOWN = ["SYNTH CAFE", "SYNTH GARAGE", "SYNTH FLORIST", "SYNTH BAKERY"]
KNOWN = "TESCO STORES"  # a seeded Merchant with a default Category


def export(rows: Sequence[tuple[str, int]]) -> str:
    """A synthetic Lloyds export: one debit per row, a day apart."""
    bal, txns = 500000, list[Txn]()
    for i, (desc, minor) in enumerate(rows):
        bal += minor
        txns.append(Txn(START + timedelta(days=i), "DEB", desc, minor, bal))
    h = History("00-00-00", "00000001", txns)
    return render_export(h, 0, len(txns)).text


@contextmanager
def ledger_with(
    rows: Sequence[tuple[str, int]],
) -> Generator[tuple[Connection, Ledger]]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "export.csv"
        p.write_text(export(rows))
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            if rows:
                led.import_files([p])
            categorise.run(conn, 1)
            yield conn, led
        engine.dispose()


def category_items(q: ReviewQueue) -> list[Item]:
    return [i for i in q.items() if i.type == "category"]


def categories(led: Ledger) -> dict[int, tuple[tuple[str, str] | None, str]]:
    out: dict[int, tuple[tuple[str, str] | None, str]] = {}
    for t in led.transactions():
        assert t.categorisation is not None
        out[t.id] = (t.categorisation.category, t.categorisation.source)
    return out


def test_uncategorised_merchants_are_queued_most_frequent_first() -> None:
    rows = [("SYNTH CAFE", -350)] * 2 + [("SYNTH GARAGE", -4000)] * 3
    rows += [(KNOWN, -1200)] * 5
    with ledger_with(rows) as (conn, _):
        items = category_items(ReviewQueue(conn, 1))
        assert [(i.title, i.weight) for i in items] == [
            ("SYNTH GARAGE", 3),
            ("SYNTH CAFE", 2),
        ]
        assert all(len(i.transaction_ids) == i.weight for i in items)
        assert items[0].id == f"category:{items[0].merchant_id}"


def test_a_merchant_answer_categorises_past_and_future_rows() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 3) as (conn, led):
        q = ReviewQueue(conn, 1)
        (item,) = category_items(q)
        v = led.ledger_version()

        q.answer(item.id, item.version, Answer(category=("Food & Drink", "Coffee")))

        assert {c for c, _ in categories(led).values()} == {("Food & Drink", "Coffee")}
        assert category_items(q) == []
        (k,) = led.corrections()
        assert k.merchant_id == item.merchant_id and k.transaction_id is None
        assert led.ledger_version() > v


def test_just_this_one_corrects_one_row_and_the_rest_stay_queued() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 3) as (conn, led):
        q = ReviewQueue(conn, 1)
        (item,) = category_items(q)
        one = item.transaction_ids[1]

        q.answer(
            item.id,
            item.version,
            Answer(category=("Food & Drink", "Coffee"), transaction_id=one),
        )

        (k,) = led.corrections()
        assert (k.transaction_id, k.merchant_id) == (one, None)
        (left,) = category_items(q)
        assert left.id == item.id and left.version != item.version
        assert set(left.transaction_ids) == set(item.transaction_ids) - {one}


def test_undo_reverts_the_correction_and_requeues_the_item() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 2) as (conn, led):
        q = ReviewQueue(conn, 1)
        before, cats = q.items(), categories(led)
        (item,) = before
        q.answer(item.id, item.version, Answer(category=("Travel", "Hotels")))

        q.undo()

        assert q.items() == before
        assert categories(led) == cats
        (k,) = led.corrections()
        assert k.reverted_at is not None
        with pytest.raises(UnknownTarget):  # nothing left to undo
            q.undo()


def test_a_skipped_item_moves_to_the_end_until_undone() -> None:
    rows = [("SYNTH CAFE", -350)] * 2 + [("SYNTH GARAGE", -4000)]
    with ledger_with(rows) as (conn, _):
        q = ReviewQueue(conn, 1)
        first, second = q.items()

        q.skip(first.id, first.version)

        assert [(i.id, i.skipped) for i in q.items()] == [
            (second.id, False),
            (first.id, True),
        ]
        q.undo()
        assert q.items() == [first, second]


def test_stale_unknown_and_malformed_answers_are_refused() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 2 + [(KNOWN, -100)]) as (conn, led):
        q = ReviewQueue(conn, 1)
        (item,) = q.items()
        known = next(
            t.id
            for t in led.transactions()
            if t.categorisation and t.categorisation.merchant == "Tesco"
        )
        cat = ("Travel", "Hotels")
        v = led.ledger_version()
        with pytest.raises(StaleItem):
            q.answer(item.id, "0" * 64, Answer(category=cat))
        with pytest.raises(UnknownTarget):
            q.answer("category:999999", item.version, Answer(category=cat))
        with pytest.raises(UnknownTarget):
            q.answer("nonsense", item.version, Answer(category=cat))
        with pytest.raises(InvalidAnswer):  # a category item needs a Category
            q.answer(item.id, item.version, Answer(choice="yes"))
        with pytest.raises(InvalidAnswer):  # another Merchant's row
            q.answer(item.id, item.version, Answer(category=cat, transaction_id=known))
        with pytest.raises(UnknownTarget):  # not a current Category
            q.answer(item.id, item.version, Answer(category=("Nope", "Nope")))
        assert led.corrections() == [] and led.ledger_version() == v
        assert q.items() == [item]


def test_a_suggested_subscription_is_queued_and_answered() -> None:
    with ledger_with([("SYNTH STREAMBOX", -999)] * 2) as (conn, led):
        m = next(x.id for x in led.merchants() if x.name == "SYNTH STREAMBOX")
        led.record_detection(
            m, "subscription", "Streaming", "pattern", "suggested", START, 2
        )
        q = ReviewQueue(conn, 1)
        sub = next(i for i in q.items() if i.type == "subscription")
        assert (sub.merchant_id, sub.weight, sub.choices) == (m, 2, ("yes", "no"))
        before = led.subscriptions()

        q.answer(sub.id, sub.version, Answer(choice="yes"))
        e = led.subscription(m)
        assert e is not None and (e.status, e.source) == ("active", "user")
        assert all(i.type != "subscription" for i in q.items())

        q.undo()
        assert led.subscriptions() == before
        assert any(i.id == sub.id for i in q.items())
        # the undone decision no longer blocks detection
        assert led.record_detection(
            m, "subscription", "Streaming", "pattern", "active", START, 3
        )


def test_a_kept_possible_duplicate_never_reappears_unless_it_changes() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 3) as (conn, led):
        ids = sorted(t.id for t in led.transactions())
        acc = led.transactions()[0].account_id
        imp = conn.execute(schema.statement_import.select()).scalars().first()
        db = led._db  # pyright: ignore[reportPrivateUsage]
        for t in ids[:2]:
            db.insert(
                schema.possible_duplicate,
                group_id=1,
                transaction_id=t,
                account_id=acc,
                statement_import_id=imp,
            )
        q = ReviewQueue(conn, 1)
        dup = next(i for i in q.items() if i.type == "possible_duplicate")
        assert (dup.transaction_ids, dup.choices) == (tuple(ids[:2]), ("keep_both",))

        q.answer(dup.id, dup.version, Answer(choice="keep_both"))
        assert all(i.type != "possible_duplicate" for i in q.items())

        db.insert(  # a later import widens the group: a new decision is needed
            schema.possible_duplicate,
            group_id=1,
            transaction_id=ids[2],
            account_id=acc,
            statement_import_id=imp,
        )
        assert any(i.type == "possible_duplicate" for i in q.items())


def test_another_user_can_neither_see_nor_answer_my_items() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 2) as (conn, led):
        mine, theirs = ReviewQueue(conn, 1), ReviewQueue(conn, 2)
        (item,) = mine.items()
        v = led.ledger_version()

        assert theirs.items() == []
        for attempt in (
            lambda: theirs.answer(
                item.id, item.version, Answer(category=("Travel", "Hotels"))
            ),
            lambda: theirs.skip(item.id, item.version),
            theirs.undo,
        ):
            with pytest.raises(UnknownTarget):
                attempt()
        mine.skip(item.id, item.version)
        with pytest.raises(UnknownTarget):  # my skip is not theirs to undo
            theirs.undo()
        assert led.corrections() == [] and led.ledger_version() == v + 1
        assert [i.skipped for i in mine.items()] == [True]


# --- property tests -------------------------------------------------------

ROWS = st.lists(
    st.tuples(st.sampled_from([*UNKNOWN, KNOWN]), st.integers(-9000, -1)),
    min_size=1,
    max_size=25,
)
LEAVES = [(top, sub) for top, subs in categorise.TAXONOMY.items() for sub in subs]
STEP = st.tuples(
    st.sampled_from(["answer", "one", "skip", "undo"]),
    st.integers(0, 10),
    st.sampled_from(LEAVES),
)


def figures(led: Ledger) -> list[object]:
    return [led.summarise_month(m) for m in led.months()]


@settings(max_examples=40, deadline=None)
@given(rows=ROWS, steps=st.lists(STEP, max_size=8))
def test_the_queue_never_changes_money_and_answers_reach_only_their_rows(
    rows: list[tuple[str, int]], steps: list[tuple[str, int, tuple[str, str]]]
) -> None:
    with ledger_with(rows) as (conn, led):
        q = ReviewQueue(conn, 1)
        money = figures(led)
        for action, pick, cat in steps:
            items = q.items()
            if action == "undo":
                try:
                    q.undo()
                except UnknownTarget:
                    pass
                continue
            if not items:
                continue
            item = items[pick % len(items)]
            before = categories(led)
            if action == "skip":
                q.skip(item.id, item.version)
                assert categories(led) == before
                continue
            one = item.transaction_ids[pick % len(item.transaction_ids)]
            q.answer(
                item.id,
                item.version,
                Answer(category=cat, transaction_id=one if action == "one" else None),
            )
            after = categories(led)
            reached = {t for t in after if after[t] != before[t]}
            intended = {one} if action == "one" else set(item.transaction_ids)
            assert reached <= intended
            assert all(after[t] == (cat, "correction") for t in intended)
            assert figures(led) == money
        assert figures(led) == money


@settings(max_examples=40, deadline=None)
@given(rows=ROWS, data=st.data())
def test_undo_restores_the_previous_state(
    rows: list[tuple[str, int]], data: st.DataObject
) -> None:
    with ledger_with(rows) as (conn, led):
        q = ReviewQueue(conn, 1)
        items = q.items()
        if not items:
            return
        item = data.draw(st.sampled_from(items))
        one = data.draw(st.none() | st.sampled_from(item.transaction_ids))
        cats, subs = categories(led), led.subscriptions()

        if data.draw(st.booleans()):
            q.skip(item.id, item.version)
        else:
            cat = data.draw(st.sampled_from(LEAVES))
            q.answer(item.id, item.version, Answer(category=cat, transaction_id=one))
        q.undo()

        assert (q.items(), categories(led), led.subscriptions()) == (
            items,
            cats,
            subs,
        )


ITEM = st.builds(
    Item,
    type=st.sampled_from(["category", "subscription", "possible_duplicate"]),
    key=st.integers(1, 50).map(str),
    version=st.just("v"),
    weight=st.integers(1, 9),
    skipped=st.booleans(),
)


@settings(max_examples=200, deadline=None)
@given(
    st.lists(ITEM, max_size=12, unique_by=lambda i: (i.type, i.key)),
    st.randoms(),
)
def test_ordering_is_deterministic_and_by_frequency(
    items: list[Item], rnd: object
) -> None:
    shuffled = list(items)
    rnd.shuffle(shuffled)  # type: ignore[attr-defined]
    ordered = order(items, by_frequency)
    assert order(shuffled, by_frequency) == ordered
    assert sorted(ordered, key=lambda i: i.id) == sorted(items, key=lambda i: i.id)
    # unskipped first, then most frequent first within each
    keys = [(i.skipped, -i.weight) for i in ordered]
    assert keys == sorted(keys)


@settings(max_examples=30, deadline=None)
@given(rows=ROWS, extra=ROWS, pick=st.integers(0, 10))
def test_an_answered_item_never_reappears_unless_its_inputs_change(
    rows: list[tuple[str, int]], extra: list[tuple[str, int]], pick: int
) -> None:
    with ledger_with(rows) as (conn, led):
        q = ReviewQueue(conn, 1)
        items = q.items()
        if not items:
            return
        item = items[pick % len(items)]
        q.answer(item.id, item.version, Answer(category=("Travel", "Hotels")))
        # later rows for the same Merchant arrive; the Merchant answer covers them
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "later.csv"
            later = [(desc, minor) for desc, minor in extra]
            text = export(rows + later)
            p.write_text(text)
            led.import_files([p])
            categorise.run(conn, 1)
        assert all(i.id != item.id for i in q.items())
        assert replace(item, skipped=False) not in q.items()
