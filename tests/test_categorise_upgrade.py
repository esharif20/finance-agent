"""Moving a real user's Ledger onto the UK base (#215): Merchant rows made by
the old base keep their ids, so every decision keyed by `merchant_id` (a
Merchant-wide Correction, "Always ask", a registry entry) stays in force, and
re-running `run()` changes nothing."""

import re
import tempfile
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.categorise.manage_links import MANAGE_LINKS
from finance_agent.ledger import Categorisation, Ledger
from finance_agent.storage import schema
from test_review_queue import export, ledger_with

GROCERIES = ("Food & Drink", "Groceries")
PHONE = ("Bills", "Phone & broadband")
OTHER_BILLS = ("Bills", "Other bills")

# The base before #215: other descriptors named their own raw Merchant rows.
OLD_BASE = {
    "Tesco": (re.compile(r"^TESCO\b"), GROCERIES),
    "Pret A Manger": (re.compile(r"^PRET\b"), ("Food & Drink", "Coffee")),
    "Deliveroo": (re.compile(r"^DELIVEROO\b"), ("Food & Drink", "Takeaway")),
    "Amazon": (re.compile(r"^(AMZN|AMAZON|AMZ\*)"), ("Shopping", "Online")),
    "Spotify": (re.compile(r"\bSPOTIFY\b"), ("Subscriptions", "Streaming")),
    "TfL": (re.compile(r"^TFL\b"), ("Transport", "Public transport")),
}
ROWS = [
    ("NETFLIX.COM", -1099),
    ("NETFLIX.COM/BILL", -1099),
    ("SAINSBURYS S/MKTS 0412", -2000),
    ("DISNEY PLUS", -799),
    ("TESCO PFS 5432", -4000),
    ("TESCO STORES 3412", -3000),
    ("CAFFE NERO 12", -350),
]


@contextmanager
def old_base_ledger(
    mp: pytest.MonkeyPatch, rows: Sequence[tuple[str, int]] = ROWS
) -> Generator[tuple[Connection, Ledger]]:
    """A Ledger categorised by the pre-#215 base, as the real user's is."""
    with ledger_with([]) as (conn, led), tempfile.TemporaryDirectory() as d:
        with mp.context() as old:
            old.setattr(categorise, "MERCHANTS", OLD_BASE)
            p = Path(d) / "export.csv"
            p.write_text(export(rows))
            led.import_files([p])
            categorise.run(conn, 1)
        yield conn, led


def by_description(led: Ledger) -> dict[str, Categorisation]:
    out: dict[str, Categorisation] = {}
    for t in led.transactions():
        assert t.categorisation is not None
        out[t.description] = t.categorisation
    return out


def test_decisions_on_old_merchant_rows_survive_the_new_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with old_base_ledger(monkeypatch) as (conn, led):
        before = by_description(led)
        mid = {d: c.merchant_id or 0 for d, c in before.items()}
        assert before["NETFLIX.COM"].merchant == "NETFLIX.COM"  # the old raw name
        assert before["TESCO PFS 5432"].merchant == "Tesco"
        led.not_a_subscription(mid["NETFLIX.COM"])
        led.correct_merchant(mid["SAINSBURYS S/MKTS 0412"], PHONE)
        led.add_subscription(mid["DISNEY PLUS"], "subscription", "Streaming")
        led.set_always_ask(mid["TESCO STORES 3412"], True)
        nero = next(
            t.id for t in led.transactions() if t.description == "CAFFE NERO 12"
        )
        led.correct_transaction(nero, PHONE)
        entries = len(led.subscriptions())

        categorise.run(conn, 1)  # the new base
        after = by_description(led)
        # decided Merchants keep their row and id; raw names become clean ones
        for d, name in (
            ("NETFLIX.COM", "Netflix"),
            ("SAINSBURYS S/MKTS 0412", "Sainsbury's"),
            ("DISNEY PLUS", "Disney Plus"),
        ):
            assert (after[d].merchant_id, after[d].merchant) == (mid[d], name)
        # the undecided NETFLIX.COM/BILL row joins the same Merchant
        assert after["NETFLIX.COM/BILL"].merchant_id == mid["NETFLIX.COM"]
        # the Merchant-wide Correction and the per-Transaction one hold
        for d in ("SAINSBURYS S/MKTS 0412", "CAFFE NERO 12"):
            assert (after[d].category, after[d].source) == (PHONE, "correction")
        # "Always ask" on Tesco still covers its fuel row
        for d in ("TESCO PFS 5432", "TESCO STORES 3412"):
            assert after[d].merchant_id == mid["TESCO STORES 3412"]
            assert after[d].source == "always_ask"
        # registry: the same entries on the same rows, none added by detection
        subs = {s.merchant_id: s for s in led.subscriptions()}
        assert len(subs) == entries
        assert subs[mid["NETFLIX.COM"]].status == "not_subscription"
        assert subs[mid["DISNEY PLUS"]].source == "user"
        # the clean name now reaches its Manage link
        assert any("Netflix" in e.names for e in MANAGE_LINKS)

        categorise.run(conn, 1)  # idempotent
        assert by_description(led) == after


def test_registry_history_survives_the_rename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # migration 0021 deletes a Merchant's registry rows with the Merchant, so
    # run() must rename rows in place, never delete or replace them
    with old_base_ledger(monkeypatch) as (conn, led):
        trigger = "subscription_entry_merchant_gone"
        assert conn.execute(
            sa.text("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=:n"),
            {"n": trigger},
        ).scalar()
        mid = {d: c.merchant_id or 0 for d, c in by_description(led).items()}
        confirmed, cancelled, dismissed = (
            mid["DISNEY PLUS"],
            mid["SAINSBURYS S/MKTS 0412"],
            mid["NETFLIX.COM"],
        )
        led.add_subscription(confirmed, "subscription", "Streaming")
        led.confirm_subscription(confirmed)
        led.add_subscription(cancelled, "subscription", "Streaming")
        led.cancel_subscription(cancelled)
        led.not_a_subscription(dismissed)
        e = schema.subscription_entry
        history = sa.select(e).order_by(e.c.id)
        rows = conn.execute(history).all()
        status = {s.merchant_id: s.status for s in led.subscriptions()}

        categorise.run(conn, 1)  # renames all three to their clean names
        names = {m.id: m.name for m in led.merchants()}
        assert (names[confirmed], names[cancelled], names[dismissed]) == (
            "Disney Plus",
            "Sainsbury's",
            "Netflix",
        )
        assert conn.execute(history).all() == rows  # every row, untouched
        assert {s.merchant_id: s.status for s in led.subscriptions()} == status
        assert (status[confirmed], status[cancelled], status[dismissed]) == (
            "active",
            "user_cancelled",
            "not_subscription",
        )


def test_a_fresh_ledger_still_gets_the_sub_brand_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # nobody decided anything about Tesco: the fuel row moves to Tesco Fuel
    with old_base_ledger(monkeypatch) as (conn, led):
        categorise.run(conn, 1)
        after = by_description(led)
        assert after["TESCO PFS 5432"].merchant == "Tesco Fuel"
        assert after["TESCO PFS 5432"].category == ("Transport", "Fuel")
        assert after["TESCO STORES 3412"].merchant == "Tesco"


DECISIONS = st.lists(
    st.tuples(
        st.sampled_from(["merchant", "always_ask", "txn", "sub"]),
        st.integers(0, len(ROWS) - 1),
    ),
    max_size=6,
)


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(DECISIONS)
def test_the_new_base_never_drops_a_decision_and_reruns_change_nothing(
    monkeypatch: pytest.MonkeyPatch, decisions: list[tuple[str, int]]
) -> None:
    with old_base_ledger(monkeypatch) as (conn, led):
        txns = sorted(led.transactions(), key=lambda t: t.id)
        for what, i in decisions:
            t = txns[i]
            assert t.categorisation is not None
            m = t.categorisation.merchant_id
            assert m is not None
            flagged = {x.id for x in led.merchants() if x.always_ask}
            if what == "merchant" and m not in flagged:
                led.correct_merchant(m, PHONE)
            elif what == "always_ask":
                led.set_always_ask(m, True)
            elif what == "txn":
                led.correct_transaction(t.id, OTHER_BILLS)
            elif what == "sub" and led.subscription(m) is None:
                led.add_subscription(m, "subscription", "Streaming")
        with monkeypatch.context() as mp:  # the old app applies the decisions
            mp.setattr(categorise, "MERCHANTS", OLD_BASE)
            categorise.run(conn, 1)
        decided = {x.id for x in led.merchants() if x.always_ask}
        decided |= {s.merchant_id for s in led.subscriptions()}
        decided |= {
            k.merchant_id
            for k in led.corrections()
            if k.merchant_id and k.reverted_at is None
        }
        old = by_description(led)
        names = {x.id: x.name for x in led.merchants()}

        categorise.run(conn, 1)  # the new base
        new = by_description(led)
        for d, was in old.items():
            if was.merchant_id in decided:  # its Merchant row and decision hold
                assert new[d].merchant_id == was.merchant_id
            if was.source in ("correction", "always_ask"):
                assert (new[d].category, new[d].source) == (was.category, was.source)
        # no Merchant row lost its name to another, and none was duplicated
        assert len({x.name for x in led.merchants()}) == len(led.merchants())
        assert {x.id for x in led.merchants()} >= names.keys()
        categorise.run(conn, 1)
        assert by_description(led) == new
