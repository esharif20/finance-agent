"""Verifier #170: a random mix of "Always ask" toggles, Merchant-wide and
per-payment Corrections, reverts and Splits through the API, over synthetic
multi-month histories with Refunds. Money identities hold every month, flagged
payments never get an automatic Category, the read-side fallback agrees with a
fresh `categorise.run`, and the assistant tools still equal the API.
Synthetic data only."""

import tempfile
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.ledger import CATEGORISED_KINDS, TO_REVIEW, Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import generate_histories
from test_assistant_tools import ME, assert_matches_api, seed

CATS = (
    ["Food & Drink", "Groceries"],
    ["Food & Drink", "Coffee"],
    ["Shopping", "Online"],
)
# answered by the user, so not automatic
ANSWERED = {"always_ask", "correction", "split", "split_refund"}

OP = st.one_of(
    st.tuples(st.just("flag"), st.integers(0, 50), st.booleans()),
    st.tuples(st.just("merchant"), st.integers(0, 50), st.integers(0, 2)),
    st.tuples(st.just("txn"), st.integers(0, 500), st.integers(0, 2)),
    st.tuples(st.just("split"), st.integers(0, 500), st.integers(0, 2)),
    st.tuples(st.just("revert"), st.integers(0, 50), st.integers(0, 0)),
)


def snapshot(engine: Engine) -> dict[str, tuple[Decimal, ...]]:
    out: dict[str, tuple[Decimal, ...]] = {}
    with engine.begin() as conn:
        led = Ledger(conn, ME)
        for m in led.months():
            s = led.summarise_month(m)
            out[m] = (s.money_in, s.money_out, s.net)
    return out


def check(engine: Engine, money: dict[str, tuple[Decimal, ...]]) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, ME)
        flagged = {m.id for m in led.merchants() if m.always_ask}
        for k in led.corrections():  # flagging switched these off for good
            assert not (k.merchant_id in flagged and k.reverted_at is None)
        txns = led.transactions()
        for t in txns:
            c = t.categorisation
            assert c is not None
            if c.merchant_id in flagged and c.kind in CATEGORISED_KINDS:
                assert c.source in ANSWERED
            if c.source == "always_ask":
                assert c.category == (TO_REVIEW, TO_REVIEW)
        for m, want in money.items():
            s = led.summarise_month(m)
            assert (s.money_in, s.money_out, s.net) == want
            b = led.category_breakdown(m)
            ring = sum((x.amount for x in b.categories), Decimal(0))
            assert ring + b.unmatched_refunds == b.spending_after_refunds
            assert b.spending_after_refunds + b.refunds + b.fees == s.money_out
            assert all(x.amount >= 0 for x in b.categories if x.name == TO_REVIEW)
            # To review holds exactly the unanswered flagged Spending, gross
            gross = -sum(
                (
                    t.amount
                    for t in txns
                    if t.posted_on.strftime("%Y-%m") == m
                    and t.categorisation
                    and t.categorisation.source == "always_ask"
                    and t.categorisation.kind == "Spending"
                ),
                Decimal(0),
            )
            got = next((x.amount for x in b.categories if x.name == TO_REVIEW), 0)
            assert got == gross
        before = led._categorisations()  # pyright: ignore[reportPrivateUsage]
        categorise.run(conn, ME)  # stored values agree with the read side
        assert led._categorisations() == before  # pyright: ignore[reportPrivateUsage]


@settings(max_examples=10, deadline=None)
@given(
    st.integers(0, 2**32 - 1),
    st.integers(40, 120),
    st.lists(OP, min_size=1, max_size=14),
)
def test_random_flags_corrections_and_splits_keep_money_and_tool_parity(
    seed_: int, days: int, ops: list[tuple[str, int, int]]
) -> None:
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        seed(engine, ME, generate_histories(seed_, days)[0], Path(d))
        money = snapshot(engine)
        made: list[int] = []
        with TestClient(api.create_app(engine)) as c:
            merchants = [m["id"] for m in c.get("/api/merchants").json()["items"]]
            with engine.begin() as conn:
                txns = [
                    (t.id, int(t.amount.scaleb(2)))
                    for t in Ledger(conn, ME).transactions()
                ]
            for op, i, arg in ops:
                if op == "flag" and merchants:
                    mid = merchants[i % len(merchants)]
                    r = c.put(f"/api/merchants/{mid}/always-ask", json={"on": arg})
                    assert r.status_code == 200
                    assert r.json()["merchant"]["always_ask"] is arg
                elif op == "merchant" and merchants:
                    body = {
                        "merchant_id": merchants[i % len(merchants)],
                        "category": CATS[arg],
                    }
                    r = c.post("/api/corrections", json=body)
                    assert r.status_code in (201, 409)
                    if r.status_code == 201:
                        made.append(r.json()["id"])
                elif op == "txn" and txns:
                    body = {
                        "transaction_id": txns[i % len(txns)][0],
                        "category": CATS[arg],
                    }
                    r = c.post("/api/corrections", json=body)
                    assert r.status_code in (201, 422)
                    if r.status_code == 201:
                        made.append(r.json()["id"])
                elif op == "split" and txns:
                    tid, amount = txns[i % len(txns)]
                    if abs(amount) < 2:
                        continue
                    parts = [
                        {"amount_minor": int(amount / 2), "category": CATS[arg]},
                        {"category": CATS[(arg + 1) % 3]},
                    ]
                    r = c.put(f"/api/splits/{tid}", json={"parts": parts})
                    assert r.status_code < 500
                elif op == "revert" and made:
                    r = c.delete(f"/api/corrections/{made[i % len(made)]}")
                    assert r.status_code < 500
                check(engine, money)
            assert_matches_api(engine, c)
        engine.dispose()
