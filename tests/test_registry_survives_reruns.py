"""Verifier #132 / migration 0021: the trigger deletes a Merchant's registry
history with it, so no routine path may delete (or REPLACE) a Merchant. Any mix
of re-categorising, embedding refresh, detection and re-importing keeps every
Merchant id and every registry row, and the user's decisions stay in force.
Synthetic data only."""

import tempfile
from pathlib import Path

import pytest
import sqlalchemy as sa
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection
from sqlalchemy.exc import IntegrityError

from finance_agent import categorise
from finance_agent.categorise import embedding, subscriptions
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db, schema
from synthetic_lloyds import generate_histories
from test_assistant_tools import ME, seed

DECISIONS = ("cancel", "not", "confirm_after_not")
OPS = ("run", "embed", "recognise", "reimport")


def registry(conn: Connection) -> set[tuple[object, ...]]:
    e = schema.subscription_entry
    cols = (e.c.id, e.c.merchant_id, e.c.status, e.c.reason, e.c.source)
    return {tuple(r) for r in conn.execute(sa.select(*cols).where(e.c.user_id == ME))}


def merchants(conn: Connection) -> dict[int, str]:
    m = schema.merchant
    rows = conn.execute(sa.select(m.c.id, m.c.name).where(m.c.user_id == ME))
    return {r.id: r.name for r in rows}


@settings(max_examples=8, deadline=None)
@given(
    st.integers(0, 2**32 - 1),
    st.lists(st.sampled_from(DECISIONS), min_size=1, max_size=4),
    st.lists(st.sampled_from(OPS), min_size=1, max_size=8),
)
def test_reruns_never_remove_merchants_or_registry_rows(
    seed_: int, decisions: list[str], ops: list[str]
) -> None:
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        seed(engine, ME, generate_histories(seed_, 60)[0], Path(d))
        exports = sorted(Path(d).glob(f"u{ME}-*.csv"))
        decided: dict[int, tuple[str, str | None]] = {}
        with engine.begin() as conn:
            led = Ledger(conn, ME)
            ids = [m.id for m in led.merchants()]
            for mid, what in zip(ids, decisions, strict=False):
                led.add_subscription(mid, "subscription", "Streaming")
                if what == "cancel":
                    led.cancel_subscription(mid)
                else:
                    led.not_a_subscription(mid)
                    if what == "confirm_after_not":
                        led.confirm_subscription(mid, "bill", "Energy")
                x = led.subscription(mid)
                assert x is not None
                decided[mid] = (x.status, x.reason)
            before_rows, before_merchants = registry(conn), merchants(conn)
        for op in ops:
            with engine.begin() as conn:
                led = Ledger(conn, ME)
                if op == "run":
                    categorise.run(conn, ME)
                elif op == "embed":
                    embedding.refresh(conn, ME, embedding.TrigramEmbedder())
                elif op == "recognise":
                    subscriptions.recognise(led)
                else:  # the same exports again: all duplicates
                    led.import_files(exports)
                    categorise.run(conn, ME)
                # detection may add rows, never take one away
                assert before_rows <= registry(conn)
                now = merchants(conn)
                assert before_merchants.items() <= now.items()
                for mid, want in decided.items():
                    x = led.subscription(mid)
                    assert x is not None and (x.status, x.reason) == want
        engine.dispose()


def test_deleting_a_merchant_still_in_use_keeps_its_registry() -> None:
    """The trigger runs inside the DELETE; when a Categorisation still names
    the Merchant the foreign key fails and the whole statement, trigger
    included, rolls back."""
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        seed(engine, ME, generate_histories(7, 60)[0], Path(d))
        with engine.begin() as conn:
            led = Ledger(conn, ME)
            mid = next(
                t.categorisation.merchant_id
                for t in led.transactions()
                if t.categorisation and t.categorisation.merchant_id
            )
            led.add_subscription(mid, "subscription", "Streaming")
            before = registry(conn)
        m = schema.merchant
        with engine.begin() as conn, pytest.raises(IntegrityError, match="FOREIGN KEY"):
            conn.execute(sa.delete(m).where(m.c.id == mid))
        with engine.begin() as conn:
            assert registry(conn) == before
        engine.dispose()
