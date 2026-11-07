"""'Always ask' Merchants (#125): the categoriser abstains for a flagged
Merchant, its payments count under "To review", and the flag is only ever set
by the user. Synthetic data only."""

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.ledger import (
    CATEGORISED_KINDS,
    TO_REVIEW,
    UNCATEGORISED,
    AlwaysAskMerchant,
    Ledger,
)
from finance_agent.ledger.review import Answer, InvalidAnswer, ReviewQueue
from test_review_queue import KNOWN, ledger_with


def merchant_id(led: Ledger, name: str) -> int:
    return next(m.id for m in led.merchants() if m.name == name)


def flag(conn: Connection, led: Ledger, mid: int, on: bool = True) -> list[int]:
    reverted = led.set_always_ask(mid, on)
    categorise.run(conn, 1)
    return reverted


def test_a_flagged_merchant_gets_no_automatic_category() -> None:
    with ledger_with([(KNOWN, -1200), ("SYNTH CAFE", -350)]) as (conn, led):
        tesco = merchant_id(led, "Tesco")
        flag(conn, led, tesco)
        got = {
            t.description: (t.categorisation.category, t.categorisation.source)
            for t in led.transactions()
            if t.categorisation
        }
        assert got[KNOWN] == ((TO_REVIEW, TO_REVIEW), "always_ask")
        assert got["SYNTH CAFE"][1] == "default"  # others unchanged
        assert next(m for m in led.merchants() if m.id == tesco).always_ask == "user"


GROCERIES = ("Food & Drink", "Groceries")
COFFEE = ("Food & Drink", "Coffee")
ONLINE = ("Shopping", "Online")


def cats(led: Ledger) -> list[tuple[tuple[str, str] | None, str]]:
    return [
        (t.categorisation.category, t.categorisation.source)
        for t in led.transactions()
        if t.categorisation
    ]


def test_flag_on_switches_off_merchant_wide_correction_but_keeps_per_payment() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 3) as (conn, led):
        cafe = merchant_id(led, "SYNTH CAFE")
        wide = led.correct_merchant(cafe, COFFEE)
        one = led.correct_transaction(led.transactions()[0].id, ONLINE)
        assert flag(conn, led, cafe) == [wide.id]
        assert (
            cats(led)
            == [(ONLINE, "correction")] + [((TO_REVIEW, TO_REVIEW), "always_ask")] * 2
        )
        active = {k.id for k in led.corrections() if k.reverted_at is None}
        assert active == {one.id}
        with pytest.raises(AlwaysAskMerchant):
            led.correct_merchant(cafe, COFFEE)
        assert {k.id for k in led.corrections() if k.reverted_at is None} == {one.id}


def test_turning_the_flag_off_restores_nothing() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 2) as (conn, led):
        cafe = merchant_id(led, "SYNTH CAFE")
        led.correct_merchant(cafe, COFFEE)
        flag(conn, led, cafe)
        assert flag(conn, led, cafe, on=False) == []
        # back to what the rules alone say; the switched-off Correction stays off
        assert cats(led) == [((UNCATEGORISED, UNCATEGORISED), "default")] * 2
        assert all(k.reverted_at is not None for k in led.corrections())
        assert next(m for m in led.merchants() if m.id == cafe).always_ask is None


def test_the_suggestion_starts_at_two_distinct_corrected_categories() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 3 + [(KNOWN, -1200)]) as (conn, led):
        cafe = merchant_id(led, "SYNTH CAFE")
        ids = [t.id for t in led.transactions() if t.description == "SYNTH CAFE"]
        led.correct_transaction(ids[0], COFFEE)
        led.correct_transaction(ids[1], COFFEE)  # same Category twice: one
        assert led.always_ask_suggested() == set()
        led.correct_merchant(cafe, ONLINE)  # a Merchant-wide one counts too
        assert led.always_ask_suggested() == {cafe}
        # only a suggestion: nothing was flagged and nothing abstains
        assert next(m for m in led.merchants() if m.id == cafe).always_ask is None
        categorise.run(conn, 1)
        assert all(s != "always_ask" for _, s in cats(led))
        flag(conn, led, cafe)  # the user accepts: no longer suggested
        assert led.always_ask_suggested() == set()


def test_a_reverted_correction_does_not_count_towards_the_suggestion() -> None:
    with ledger_with([("SYNTH CAFE", -350)] * 2) as (_, led):
        a, b = (t.id for t in led.transactions())
        led.correct_transaction(a, COFFEE)
        led.revert_correction(led.correct_transaction(b, ONLINE).id)
        assert led.always_ask_suggested() == set()


POOL = [
    ("SYNTH CAFE", -350),
    ("SYNTH CAFE REFUND", 350),
    (KNOWN, -1200),
    ("SYNTH GARAGE", -4000),
    ("SYNTH GARAGE REFUND", 1500),
    ("SYNTH PARKING", -600),
]
MONTH = "2026-03"


def to_review(led: Ledger) -> Decimal:
    b = led.category_breakdown(MONTH)
    return next((c.amount for c in b.categories if c.name == TO_REVIEW), Decimal(0))


def check_totals(led: Ledger, money: tuple[Decimal, Decimal, Decimal]) -> None:
    s = led.summarise_month(MONTH)
    assert (s.money_in, s.money_out, s.net) == money
    b = led.category_breakdown(MONTH)
    ring = sum((c.amount for c in b.categories), Decimal(0))
    assert ring + b.unmatched_refunds == b.spending_after_refunds
    assert b.spending_after_refunds + b.refunds + b.fees == s.money_out
    assert all(c.amount >= 0 for c in b.categories if c.name == TO_REVIEW)


@settings(max_examples=40, deadline=None)
@given(
    rows=st.lists(st.sampled_from(POOL), min_size=1, max_size=12),
    flips=st.lists(st.tuples(st.sampled_from(POOL), st.booleans()), max_size=6),
    per_payment=st.booleans(),
)
def test_flagging_never_changes_money_and_flagged_merchants_get_no_category(
    rows: list[tuple[str, int]],
    flips: list[tuple[tuple[str, int], bool]],
    per_payment: bool,
) -> None:
    with ledger_with(rows) as (conn, led):
        s = led.summarise_month(MONTH)
        money = (s.money_in, s.money_out, s.net)
        check_totals(led, money)
        first = led.transactions()[0]
        fixed = per_payment and first.categorisation is not None
        if fixed:  # a per-payment Correction outlives any flag
            led.correct_transaction(first.id, ONLINE)
        for (desc, _), on in flips:
            m = categorise.normalise(desc)
            mid = next((x.id for x in led.merchants() if x.name == m), None)
            if mid is None:
                continue
            flag(conn, led, mid, on)
            check_totals(led, money)
            flagged = {x.id for x in led.merchants() if x.always_ask}
            gross = Decimal(0)
            for t in led.transactions():
                c = t.categorisation
                assert c is not None
                if c.merchant_id in flagged and c.kind in CATEGORISED_KINDS:
                    if fixed and t.id == first.id:
                        assert (c.category, c.source) == (ONLINE, "correction")
                    else:
                        assert (c.category, c.source) == (
                            (TO_REVIEW, TO_REVIEW),
                            "always_ask",
                        )
                        gross -= t.amount if c.kind == "Spending" else 0
            # gross spending: a To review Refund is kept off the ring, unmatched
            assert to_review(led) == gross


def test_a_to_review_refund_is_unmatched_not_netted() -> None:
    rows = [("SYNTH CAFE", -350), ("SYNTH CAFE REFUND", 200)]
    with ledger_with(rows) as (conn, led):
        flag(conn, led, merchant_id(led, "SYNTH CAFE"))
        b = led.category_breakdown(MONTH)
        assert [(c.name, c.amount) for c in b.categories] == [
            (TO_REVIEW, Decimal("3.50"))
        ]
        assert b.unmatched_refunds == Decimal("-2.00")


def test_a_split_on_a_flagged_merchant_counts_by_parts() -> None:
    with ledger_with([("SYNTH CAFE", -1000), ("SYNTH CAFE", -500)]) as (conn, led):
        flag(conn, led, merchant_id(led, "SYNTH CAFE"))
        tid = led.transactions()[0].id
        led.split_transaction(tid, [(-700, GROCERIES), (None, ONLINE)])
        b = led.category_breakdown(MONTH)
        got = {sub.name: sub.amount for c in b.categories for sub in c.children}
        assert got == {
            "Groceries": Decimal("7.00"),
            "Online": Decimal("3.00"),
            TO_REVIEW: Decimal("5.00"),  # only the unsplit payment
        }


def test_each_payment_of_a_flagged_merchant_is_a_what_was_this_item() -> None:
    rows = [("SYNTH CAFE", -350), ("SYNTH CAFE", -900), ("SYNTH GARAGE", -100)]
    with ledger_with(rows) as (conn, led):
        cafe = merchant_id(led, "SYNTH CAFE")
        a, b, _ = (t.id for t in led.transactions())
        q = ReviewQueue(conn, 1)
        assert {i.type for i in q.items()} == {"category"}
        flag(conn, led, cafe)
        asks = [i for i in q.items() if i.type == "always_ask"]
        assert [(i.title, i.merchant_id, i.transaction_ids) for i in asks] == [
            ("SYNTH CAFE", cafe, (a,)),
            ("SYNTH CAFE", cafe, (b,)),
        ]
        # the flagged Merchant is not also a "category" item
        assert [i.title for i in q.items() if i.type == "category"] == ["SYNTH GARAGE"]
        first = asks[0]
        with pytest.raises(InvalidAnswer):
            q.answer(first.id, first.version, Answer(choice="yes"))
        with pytest.raises(InvalidAnswer):  # never another payment
            q.answer(first.id, first.version, Answer(COFFEE, transaction_id=b))
        q.answer(first.id, first.version, Answer(COFFEE))
        assert [i.id for i in q.items() if i.type == "always_ask"] == [asks[1].id]
        assert cats(led)[:2] == [
            (COFFEE, "correction"),
            ((TO_REVIEW, TO_REVIEW), "always_ask"),
        ]
        assert all(k.merchant_id is None for k in led.corrections())  # per payment
        q.undo()
        assert [i.id for i in q.items() if i.type == "always_ask"] == [
            asks[0].id,
            asks[1].id,
        ]
        led.split_transaction(b, [(-400, GROCERIES), (None, ONLINE)])
        assert [i.id for i in q.items() if i.type == "always_ask"] == [asks[0].id]
