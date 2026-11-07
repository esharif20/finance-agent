"""Verifier for #123: a random mix of Splits, Merchant and Transaction
Corrections, refunds and reverts never moves money in, money out, net or the
total spent away from the figures computed from the raw Transactions, in any
month, and reverting everything restores the exact earlier totals."""

import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.ledger import UNCATEGORISED, Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import History, Txn, render_export
from test_splits import LEAVES, cut

KNOWN = "TESCO STORES"
# (day, descriptor, signed minor units); positive at a Merchant is a Refund
ROWS = [
    (date(2026, 2, 27), KNOWN, -2500),
    (date(2026, 3, 2), KNOWN, -1200),
    (date(2026, 3, 3), "SYNTH CAFE", -457),
    (date(2026, 3, 5), KNOWN, 300),  # a Refund
    (date(2026, 3, 30), KNOWN, -9999),
    (date(2026, 4, 1), KNOWN, 800),  # a Refund in the next month
    (date(2026, 4, 2), "SYNTH CAFE", 120),
]
MONTHS = ("2026-02", "2026-03", "2026-04")


@contextmanager
def ledger(
    rows: Sequence[tuple[date, str, int]],
) -> Generator[tuple[Connection, Ledger]]:
    bal, txns = 500000, list[Txn]()
    for day, desc, minor in rows:
        bal += minor
        txns.append(Txn(day, "DEB", desc, minor, bal))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "export.csv"
        p.write_text(
            render_export(History("00-00-00", "00000001", txns), 0, len(txns)).text
        )
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            led.import_files([p])
            categorise.run(conn, 1)
            yield conn, led
        engine.dispose()


def raw(led: Ledger, month: str) -> tuple[Decimal, Decimal, Decimal]:
    """(money in, money out, spent net of refunds) straight from the rows."""
    rows = [t for t in led.transactions() if t.posted_on.strftime("%Y-%m") == month]
    ins = sum((t.amount for t in rows if t.amount > 0), Decimal(0))
    outs = -sum((t.amount for t in rows if t.amount < 0), Decimal(0))
    spent = -sum(
        (
            t.amount
            for t in rows
            if t.categorisation and t.categorisation.kind in ("Spending", "Refund")
        ),
        Decimal(0),
    )
    return ins, outs, spent


def check(led: Ledger) -> None:
    by_id = {t.id: t for t in led.transactions()}
    for month in MONTHS:
        ins, outs, spent = raw(led, month)
        s = led.summarise_month(month)
        assert (s.money_in, s.money_out, s.net) == (ins, outs, ins - outs)
        b = led.category_breakdown(month)
        assert b.spending_after_refunds == spent
        assert sum((c.amount for c in b.categories), b.unmatched_refunds) == spent
        # Uncategorised on the ring is gross Spending only (never a Refund)
        gross = Decimal(0)
        for t in by_id.values():
            c = t.categorisation
            if c and c.kind == "Spending" and t.posted_on.strftime("%Y-%m") == month:
                for p in c.parts or ():
                    if p.category[0] == UNCATEGORISED:
                        gross -= Decimal(p.amount_minor).scaleb(-2)
                if not c.parts and c.category and c.category[0] == UNCATEGORISED:
                    gross -= t.amount
        shown = {c.name: c.amount for c in b.categories}.get(UNCATEGORISED, Decimal(0))
        assert shown == gross
    for t in by_id.values():
        c = t.categorisation
        assert c is not None
        if c.parts:
            assert sum(p.amount_minor for p in c.parts) == int(t.amount.scaleb(2))
        if c.refund_of is not None:  # never another Account's or a later purchase
            assert not c.parts  # a Refund the user split is answered already
            p = by_id[c.refund_of]
            split = led.split(p.id)
            assert split is not None
            assert c.category in {x.category for x in split.parts}
            assert p.account_id == t.account_id and p.posted_on <= t.posted_on
            assert p.categorisation and p.categorisation.merchant_id == c.merchant_id


Op = tuple[str, int, list[tuple[int, tuple[str, str]]] | tuple[str, str] | None]


@st.composite
def ops(draw: st.DrawFn) -> list[Op]:
    out: list[Op] = []
    for _ in range(draw(st.integers(1, 8))):
        i = draw(st.integers(0, len(ROWS) - 1))
        what = draw(st.sampled_from(["split", "unsplit", "txn", "merchant", "revert"]))
        if what == "split":
            amounts = draw(cut(ROWS[i][2]))
            cats = draw(
                st.lists(
                    st.sampled_from(LEAVES),
                    min_size=len(amounts),
                    max_size=len(amounts),
                )
            )
            out.append((what, i, list(zip(amounts, cats, strict=True))))
        elif what in ("txn", "merchant"):
            out.append((what, i, draw(st.sampled_from(LEAVES))))
        else:
            out.append((what, i, None))
    return out


@settings(max_examples=60, deadline=None)
@given(ops())
def test_any_mix_keeps_every_month_equal_to_the_raw_transactions(
    sequence: list[Op],
) -> None:
    with ledger(ROWS) as (_, led):
        ids = [t.id for t in led.transactions()]
        before = {m: led.category_breakdown(m) for m in MONTHS}
        check(led)
        for what, i, arg in sequence:
            t = led.transactions()[i]
            c = t.categorisation
            if what == "split" and isinstance(arg, list):
                if c and c.kind in ("Spending", "Refund"):
                    led.split_transaction(ids[i], arg)
            elif what == "unsplit" and led.split(ids[i]) is not None:
                led.revert_split(ids[i])
            elif (
                what == "txn"
                and isinstance(arg, tuple)
                and c
                and c.kind in ("Spending", "Refund")
            ):
                led.correct_transaction(ids[i], arg)
            elif what == "merchant" and isinstance(arg, tuple) and c and c.merchant_id:
                led.correct_merchant(c.merchant_id, arg)
            elif what == "revert":
                live = [k for k in led.corrections() if k.reverted_at is None]
                if live:
                    led.revert_correction(live[i % len(live)].id)
            check(led)
        for s in led.splits():
            led.revert_split(s.transaction_id)
        for k in led.corrections():
            if k.reverted_at is None:
                led.revert_correction(k.id)
        assert {m: led.category_breakdown(m) for m in MONTHS} == before


def test_an_uncategorised_largest_refund_part_is_not_shown_as_spending() -> None:
    uncat = (UNCATEGORISED, UNCATEGORISED)
    with ledger(ROWS) as (_, led):
        refund = led.transactions()[3]
        led.split_transaction(
            refund.id, [(200, uncat), (None, ("Food & Drink", "Groceries"))]
        )
        check(led)


def test_a_refund_the_user_split_is_not_asked_which_part() -> None:
    with ledger(ROWS) as (_, led):
        purchase, refund = led.transactions()[1], led.transactions()[3]
        led.split_transaction(
            purchase.id,
            [(-700, ("Food & Drink", "Groceries")), (None, ("Shopping", "Online"))],
        )
        led.split_transaction(
            refund.id,
            [(100, ("Shopping", "Online")), (None, ("Food & Drink", "Groceries"))],
        )
        c = led.transactions()[3].categorisation
        assert c is not None and (c.source, c.refund_of) == ("split", None)
        check(led)
