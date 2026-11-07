"""Splits (#123): a Split Correction divides one Transaction into 2-10 parts in
integer minor units; totals read the parts, the Transaction never changes
(ADR 0001), and the remainder is plain code, never a model (ADR 0007)."""

import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.ledger import (
    InvalidSplit,
    KindHasNoCategory,
    Ledger,
    Spend,
    UnknownTarget,
    split_parts,
)
from finance_agent.ledger.review import Answer, InvalidAnswer, Item, ReviewQueue
from finance_agent.storage import open_db, schema
from synthetic_lloyds import generate_histories, render_export
from test_review_queue import KNOWN, ledger_with

GROCERIES = ("Food & Drink", "Groceries")
GIFTS = ("Shopping", "Online")
HOME = ("Shopping", "Household")
MONTH = "2026-03"


def totals(led: Ledger) -> dict[str, Decimal]:
    return {s.name: s.amount for s in led.spending_by_category(MONTH)}


def subs(spends: Sequence[Spend], top: str) -> dict[str, Decimal]:
    return {c.name: c.amount for s in spends if s.name == top for c in s.children}


def test_a_split_moves_each_part_into_its_category_and_computes_the_remainder() -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        (t,) = led.transactions()
        before = led.summarise_month(MONTH).money_out

        split = led.split_transaction(t.id, [(-700, GROCERIES), (None, GIFTS)])

        assert [(p.amount_minor, p.category) for p in split.parts] == [
            (-700, GROCERIES),
            (-500, GIFTS),  # the remainder, computed by the Ledger
        ]
        assert not split.needs_recheck
        assert totals(led) == {
            "Food & Drink": Decimal("7.00"),
            "Shopping": Decimal("5.00"),
        }
        assert led.summarise_month(MONTH).money_out == before
        assert led.transactions()[0].amount == Decimal("-12.00")  # unchanged


@pytest.mark.parametrize(
    "amounts",
    [
        [-1200, 0],  # a zero part
        [-1300, 100],  # mixed sign
        [-120] * 11,  # eleven parts
        [-1200],  # one part
        [-700.0, None],  # a float
        [Decimal(-700), None],  # not an int either
        [True, None],  # a bool is not an amount
        [-700, -400],  # a wrong sum
        [-(2**80), None],  # a huge int
        [None, -500],  # only the last may be the remainder
        [-1200, None],  # the remainder would be zero
    ],
)
def test_malformed_parts_are_refused_and_nothing_is_written(
    amounts: list[Any],
) -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        (t,) = led.transactions()
        version = led.ledger_version()
        cats = [GROCERIES] * len(amounts)
        with pytest.raises(InvalidSplit):
            led.split_transaction(t.id, list(zip(amounts, cats, strict=True)))
        assert led.splits() == []
        assert led.ledger_version() == version


def test_an_unknown_category_or_transaction_is_unknown() -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        (t,) = led.transactions()
        with pytest.raises(UnknownTarget):
            led.split_transaction(t.id, [(-700, GROCERIES), (None, ("Nope", "No"))])
        with pytest.raises(UnknownTarget):
            led.split_transaction(t.id + 99, [(-700, GROCERIES), (None, GIFTS)])


def test_a_transfer_cannot_be_split() -> None:
    current, saver = generate_histories(0, 10)
    with tempfile.TemporaryDirectory() as d:
        paths: list[Path] = []
        for i, h in enumerate((saver, current)):
            p = Path(d) / f"export{i}.csv"
            p.write_text(render_export(h, 0, len(h.txns)).text)
            paths.append(p)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            led.import_files(paths)
            categorise.run(conn, 1)
            leg = next(t for t in led.transactions() if t.transfer_with is not None)
            total = int(leg.amount.scaleb(2))
            with pytest.raises((InvalidSplit, KindHasNoCategory)):
                led.split_transaction(leg.id, [(total // 2, GROCERIES), (None, GIFTS)])
            assert led.splits() == []
        engine.dispose()


def test_re_splitting_replaces_and_reverting_restores_exact_totals() -> None:
    with ledger_with([(KNOWN, -1200), ("SYNTH CAFE", -450)]) as (_, led):
        t = led.transactions()[0]
        before = led.spending_by_category(MONTH)
        first = led.split_transaction(t.id, [(-700, GROCERIES), (None, GIFTS)])
        second = led.split_transaction(
            t.id, [(-100, GROCERIES), (-100, HOME), (None, GIFTS)]
        )
        assert led.splits() == [second]
        assert subs(led.spending_by_category(MONTH), "Shopping") == {
            "Online": Decimal("10.00"),
            "Household": Decimal("1.00"),
        }
        assert led.revert_split(t.id) == second.id
        assert led.spending_by_category(MONTH) == before
        assert led.split(t.id) is None
        with pytest.raises(UnknownTarget):
            led.revert_split(t.id)
        assert first.id != second.id


def test_a_split_beats_a_category_correction_on_its_transaction() -> None:
    with ledger_with([(KNOWN, -1200)]) as (_, led):
        (t,) = led.transactions()
        led.correct_transaction(t.id, ("Travel", "Hotels"))
        s = led.split_transaction(t.id, [(-700, GROCERIES), (None, GIFTS)])
        c = led.transactions()[0].categorisation
        assert c is not None
        assert (c.source, c.category) == ("split", GROCERIES)
        with pytest.raises(UnknownTarget):  # not a Category Correction
            led.revert_correction(s.id)
        led.revert_split(t.id)
        assert totals(led) == {"Travel": Decimal("12.00")}


def set_amount(conn: Connection, transaction_id: int, minor: int) -> None:
    """Stands in for a settled pre-authorisation or an added tip: no Ledger
    path changes an amount yet, so the test does it directly."""
    t = schema.transaction
    conn.execute(t.update().where(t.c.id == transaction_id).values(amount_minor=minor))


def test_a_changed_amount_needs_recheck_and_counts_whole_under_the_largest_part() -> (
    None
):
    with ledger_with([(KNOWN, -1200)]) as (conn, led):
        (t,) = led.transactions()
        made = led.split_transaction(t.id, [(-400, GROCERIES), (None, GIFTS)])
        set_amount(conn, t.id, -1500)

        (s,) = led.splits()
        assert s.needs_recheck
        assert s.parts == made.parts  # never rescaled
        assert totals(led) == {"Shopping": Decimal("15.00")}
        assert led.summarise_month(MONTH).money_out == Decimal("15.00")


def test_a_refund_of_a_split_counts_under_its_largest_part_until_answered() -> None:
    with ledger_with([(KNOWN, -1200), (KNOWN, 300)]) as (_, led):
        purchase, refund = led.transactions()
        assert refund.categorisation and refund.categorisation.kind == "Refund"
        led.split_transaction(purchase.id, [(-400, GROCERIES), (None, GIFTS)])

        c = led.transactions()[1].categorisation
        assert c is not None
        assert (c.category, c.source, c.refund_of) == (
            GIFTS,
            "split_refund",
            purchase.id,
        )
        assert totals(led) == {
            "Shopping": Decimal("5.00"),
            "Food & Drink": Decimal("4.00"),
        }

        led.correct_transaction(refund.id, GROCERIES)  # "it was the groceries"
        assert totals(led) == {
            "Shopping": Decimal("8.00"),
            "Food & Drink": Decimal("1.00"),
        }


def test_another_users_transaction_cannot_be_split_or_read() -> None:
    with ledger_with([(KNOWN, -1200)]) as (conn, led):
        (t,) = led.transactions()
        led.split_transaction(t.id, [(-700, GROCERIES), (None, GIFTS)])
        other = Ledger(conn, 2)
        with pytest.raises(UnknownTarget):
            other.split_transaction(t.id, [(-700, GROCERIES), (None, GIFTS)])
        with pytest.raises(UnknownTarget):
            other.revert_split(t.id)
        assert other.splits() == [] and other.split(t.id) is None
        assert led.split(t.id) is not None


# --- properties --------------------------------------------------------------

LEAVES = [(top, sub) for top, subs in categorise.TAXONOMY.items() for sub in subs]


@st.composite
def cut(draw: st.DrawFn, total: int) -> list[int]:
    """A valid split of `total` (non-zero) into 2-10 parts of its sign."""
    size = abs(total)
    n = draw(st.integers(2, min(10, size)))
    points = sorted(
        draw(st.sets(st.integers(1, size - 1), min_size=n - 1, max_size=n - 1))
    )
    bounds = [0, *points, size]
    sign = -1 if total < 0 else 1
    return [sign * (b - a) for a, b in zip(bounds, bounds[1:], strict=False)]


@given(
    st.integers(2, 10**12).flatmap(lambda m: st.tuples(st.just(m), st.booleans())),
    st.data(),
)
def test_any_valid_split_adds_up_exactly(
    size_neg: tuple[int, bool], data: st.DataObject
) -> None:
    size, negative = size_neg
    total = -size if negative else size
    parts = data.draw(cut(total))
    assert split_parts(total, [*parts[:-1], None]) == parts
    assert split_parts(total, parts) == parts
    assert sum(parts) == total
    assert all(p != 0 and (p < 0) == (total < 0) for p in parts)


ROWS = [(KNOWN, -1200), ("SYNTH CAFE", -457), (KNOWN, -9999)]


@st.composite
def ops(draw: st.DrawFn) -> list[tuple[int, list[tuple[int, tuple[str, str]]] | None]]:
    """A sequence of (row index, parts to split into, or None to revert)."""
    out: list[tuple[int, list[tuple[int, tuple[str, str]]] | None]] = []
    for _ in range(draw(st.integers(1, 6))):
        i = draw(st.integers(0, len(ROWS) - 1))
        if draw(st.booleans()):
            amounts = draw(cut(ROWS[i][1]))
            cats = draw(
                st.lists(
                    st.sampled_from(LEAVES),
                    min_size=len(amounts),
                    max_size=len(amounts),
                )
            )
            out.append((i, list(zip(amounts, cats, strict=True))))
        else:
            out.append((i, None))
    return out


@settings(max_examples=25, deadline=None)
@given(ops())
def test_money_out_is_unchanged_and_reverting_restores_exact_totals(
    sequence: list[tuple[int, list[tuple[int, tuple[str, str]]] | None]],
) -> None:
    with ledger_with(ROWS) as (_, led):
        ids = [t.id for t in led.transactions()]
        money_out = led.summarise_month(MONTH).money_out
        before = led.spending_by_category(MONTH)
        donut = led.category_breakdown(MONTH)
        for i, parts in sequence:
            if parts is None:
                if led.split(ids[i]) is not None:
                    led.revert_split(ids[i])
            else:
                led.split_transaction(ids[i], parts)
            assert led.summarise_month(MONTH).money_out == money_out
            spent = led.category_breakdown(MONTH)
            assert spent.spending_after_refunds == donut.spending_after_refunds
            for s in led.splits():
                assert sum(p.amount_minor for p in s.parts) == s.amount_minor
        for s in led.splits():
            led.revert_split(s.transaction_id)
        assert led.spending_by_category(MONTH) == before
        assert led.category_breakdown(MONTH) == donut


@settings(max_examples=25, deadline=None)
@given(st.data(), st.integers(-(10**9), 10**9).filter(lambda d: d != 0))
def test_a_changed_amount_always_needs_recheck_and_is_never_rescaled(
    data: st.DataObject, delta: int
) -> None:
    with ledger_with([(KNOWN, -1200)]) as (conn, led):
        (t,) = led.transactions()
        amounts = data.draw(cut(-1200))
        cats = [GROCERIES] * len(amounts)
        made = led.split_transaction(t.id, list(zip(amounts, cats, strict=True)))
        set_amount(conn, t.id, -1200 + delta)
        (s,) = led.splits()
        assert s.needs_recheck
        assert s.parts == made.parts
        c = led.transactions()[0].categorisation
        assert c is not None
        assert [p.amount_minor for p in c.parts] == [-1200 + delta]


# --- Review Queue items (#110) -------------------------------------------------


def of_type(q: ReviewQueue, kind: str) -> list[Item]:
    return [i for i in q.items() if i.type == kind]


def test_a_split_that_no_longer_adds_up_is_queued_and_revert_can_be_undone() -> None:
    with ledger_with([(KNOWN, -1200)]) as (conn, led):
        (t,) = led.transactions()
        made = led.split_transaction(t.id, [(-400, GROCERIES), (None, GIFTS)])
        q = ReviewQueue(conn, 1)
        assert of_type(q, "split_recheck") == []
        set_amount(conn, t.id, -1500)

        (item,) = of_type(q, "split_recheck")
        assert (item.title, item.transaction_ids, item.choices) == (
            "Tesco",  # the Merchant, not the raw description
            (t.id,),
            ("revert",),
        )
        with pytest.raises(InvalidAnswer):
            q.answer(item.id, item.version, Answer(category=GROCERIES))
        q.answer(item.id, item.version, Answer(choice="revert"))
        assert led.split(t.id) is None
        assert of_type(q, "split_recheck") == []

        q.undo()
        back = led.split(t.id)
        assert back is not None and back.parts == made.parts and back.needs_recheck
        assert len(of_type(q, "split_recheck")) == 1


def test_re_splitting_to_the_new_amount_resolves_the_recheck_item() -> None:
    with ledger_with([(KNOWN, -1200)]) as (conn, led):
        (t,) = led.transactions()
        led.split_transaction(t.id, [(-400, GROCERIES), (None, GIFTS)])
        set_amount(conn, t.id, -1500)
        led.split_transaction(t.id, [(-400, GROCERIES), (None, GIFTS)])
        assert of_type(ReviewQueue(conn, 1), "split_recheck") == []


def test_a_refund_of_a_split_asks_which_part_and_the_answer_can_be_undone() -> None:
    with ledger_with([(KNOWN, -1200), (KNOWN, 300)]) as (conn, led):
        purchase, refund = led.transactions()
        led.split_transaction(purchase.id, [(-400, GROCERIES), (None, GIFTS)])
        q = ReviewQueue(conn, 1)

        (item,) = of_type(q, "split_refund")
        assert item.transaction_ids == (refund.id, purchase.id)
        assert item.choices == ()  # answered with one of the parts' Categories
        with pytest.raises(InvalidAnswer):  # not one of the parts
            q.answer(item.id, item.version, Answer(category=("Travel", "Hotels")))
        q.answer(item.id, item.version, Answer(category=GROCERIES))
        assert of_type(q, "split_refund") == []
        assert totals(led)["Food & Drink"] == Decimal("1.00")

        q.undo()
        assert len(of_type(q, "split_refund")) == 1
        assert totals(led)["Shopping"] == Decimal("5.00")


def test_split_items_are_per_user() -> None:
    with ledger_with([(KNOWN, -1200), (KNOWN, 300)]) as (conn, led):
        purchase, _ = led.transactions()
        led.split_transaction(purchase.id, [(-400, GROCERIES), (None, GIFTS)])
        set_amount(conn, purchase.id, -1500)
        mine = {i.type for i in ReviewQueue(conn, 1).items()}
        assert {"split_recheck", "split_refund"} <= mine
        assert ReviewQueue(conn, 2).items() == []
