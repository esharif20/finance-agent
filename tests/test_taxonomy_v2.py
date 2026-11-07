"""Taxonomy v2 (#102): Subscriptions groups and a Bills Category. Corrections
made on v1 Categories keep their rows and resolve to the v2 names (ADR 0001,
ADR 0005)."""

import tempfile
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine, select

from finance_agent import api, categorise
from finance_agent.ledger import Ledger, taxonomy
from finance_agent.storage import Scoped, open_db, schema
from synthetic_lloyds import generate_histories, render_export


def _leaves(tax: dict[str, tuple[str, ...]]) -> set[tuple[str, str]]:
    return {(top, sub) for top, subs in tax.items() for sub in subs}


def test_v2_has_the_subscription_groups_and_a_bills_category() -> None:
    assert categorise.TAXONOMY_VERSION == 2
    assert categorise.TAXONOMY["Subscriptions"] == (
        "Streaming",
        "Software",
        "AI tools",
        "Memberships",
        "Cloud storage",
        "Other subscriptions",
    )
    assert categorise.TAXONOMY["Bills"] == (
        "Rent or mortgage",
        "Energy",
        "Water",
        "Council tax",
        "Phone & broadband",
        "Insurance",
        "Other bills",
    )
    assert "Bills & Utilities" not in categorise.TAXONOMY


def test_every_v1_category_resolves_to_a_v2_category() -> None:
    v2 = _leaves(taxonomy.TAXONOMIES[2])
    for leaf in _leaves(taxonomy.TAXONOMIES[1]):
        assert taxonomy.current(1, leaf) in v2, leaf


def test_renamed_v1_categories_keep_their_meaning() -> None:
    assert taxonomy.current(1, ("Bills & Utilities", "Energy")) == ("Bills", "Energy")
    assert taxonomy.current(1, ("Bills & Utilities", "Phone & internet")) == (
        "Bills",
        "Phone & broadband",
    )
    assert taxonomy.current(1, ("Housing", "Rent")) == ("Bills", "Rent or mortgage")
    assert taxonomy.current(1, ("Housing", "Mortgage")) == ("Bills", "Rent or mortgage")
    assert taxonomy.current(1, ("Housing", "Maintenance")) == ("Housing", "Maintenance")
    assert taxonomy.current(1, ("Subscriptions", "Streaming")) == (
        "Subscriptions",
        "Streaming",
    )
    assert taxonomy.current(2, ("Bills", "Energy")) == ("Bills", "Energy")


def test_rules_and_seeded_merchants_name_current_categories() -> None:
    v2 = _leaves(categorise.TAXONOMY)
    assert {cat for _, _, cat in categorise.RULES} <= v2
    assert {cat for _, cat in categorise.MERCHANTS.values()} <= v2


V1_LEAVES = sorted(_leaves(taxonomy.TAXONOMIES[1]))


def _raw_corrections(conn: Connection) -> list[tuple[object, ...]]:
    # the stored rows themselves: the invariant is that the upgrade never edits them
    k = schema.correction
    return [
        tuple(r)
        for r in conn.execute(
            select(
                k.c.id,
                k.c.category_id,
                k.c.transaction_id,
                k.c.merchant_id,
                k.c.reverted_at,
            ).order_by(k.c.id)
        )
    ]


@st.composite
def v1_corrections(draw: st.DrawFn) -> list[tuple[str, int, tuple[str, str], bool]]:
    """(target kind, target index, v1 Category, reverted) per Correction."""
    return draw(
        st.lists(
            st.tuples(
                st.sampled_from(["txn", "merchant"]),
                st.integers(0, 50),
                st.sampled_from(V1_LEAVES),
                st.booleans(),
            ),
            max_size=12,
        )
    )


@settings(max_examples=25, deadline=None)
@given(v1_corrections(), st.sampled_from(V1_LEAVES))
def test_upgrading_to_v2_never_loses_or_repoints_a_correction(
    fixes: list[tuple[str, int, tuple[str, str], bool]],
    merchant_default: tuple[str, str],
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = open_db(Path(tmp) / "ledger.db")
        export = Path(tmp) / "export.csv"
        h, _ = generate_histories(seed=3, days=40)
        export.write_text(render_export(h, 0, 60).text)
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            ledger.import_files([export])
            v1 = categorise.seed_taxonomy(conn, 1, version=1)
            db = Scoped(conn, 1)
            merchants = [
                db.insert(
                    schema.merchant,
                    name=f"SYNTH SHOP {i}",
                    default_category_id=v1[merchant_default],
                )
                for i in range(3)
            ]
            txns = [t.id for t in ledger.transactions()]
            expected: dict[int, tuple[str, str]] = {}
            for kind, i, leaf, reverted in fixes:
                k = (
                    ledger.correct_transaction(txns[i % len(txns)], leaf)
                    if kind == "txn"
                    else ledger.correct_merchant(merchants[i % 3], leaf)
                )
                if reverted:
                    ledger.revert_correction(k.id)
                expected[k.id] = leaf
            before = _raw_corrections(conn)

            categorise.run(conn, 1)  # seeds v2 and recategorises everything

            assert _raw_corrections(conn) == before  # none lost, none re-pointed
            v2 = _leaves(categorise.TAXONOMY)
            got = {k.id: k.category for k in ledger.corrections()}
            assert got == {i: taxonomy.current(1, c) for i, c in expected.items()}
            assert set(got.values()) <= v2
            defaults = {m.id: m.default_category for m in ledger.merchants()}
            for mid in merchants:  # a v1 default still names a v2 Category
                assert defaults[mid] == taxonomy.current(1, merchant_default)
            # the latest active Correction for a Transaction decides its Category
            active = {
                k.transaction_id: k.category
                for k in ledger.corrections()
                if k.reverted_at is None and k.transaction_id is not None
            }
            for t in ledger.transactions():
                c = t.categorisation
                assert c is not None and (c.category is None or c.category in v2)
                if t.id in active and c.kind in ("Spending", "Refund"):
                    assert (c.category, c.source) == (active[t.id], "correction")
        engine.dispose()


def test_api_serves_v2_and_accepts_a_new_category_before_the_next_import(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    h, _ = generate_histories(seed=3, days=40)
    with engine.begin() as conn:  # a database last categorised under v1
        Ledger(conn, 1).import_files([write_export(render_export(h, 0, 60).text)])
        categorise.seed_taxonomy(conn, 1, version=1)
        tid = Ledger(conn, 1).transactions()[0].id
    with TestClient(api.create_app(engine)) as client:
        cats = {c["name"]: c["subs"] for c in client.get("/api/categories").json()}
        assert cats["Bills"][0] == "Rent or mortgage"
        assert "AI tools" in cats["Subscriptions"]
        new = client.post(
            "/api/corrections",
            json={"transaction_id": tid, "category": ["Subscriptions", "AI tools"]},
        )
        assert new.status_code == 201
        old = client.post(  # a v1-only name is no longer offered
            "/api/corrections",
            json={"transaction_id": tid, "category": ["Housing", "Rent"]},
        )
        assert old.status_code == 404
    with engine.begin() as conn:
        got = Ledger(conn, 1).corrections()
        assert [k.category for k in got] == [("Subscriptions", "AI tools")]
