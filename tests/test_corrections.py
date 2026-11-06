"""Corrections overlay the Ledger (ADR 0001) and beat every rule (ADR 0005)."""

import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine

from finance_agent import categorise
from finance_agent.ledger import Ledger, Spend, Transaction
from finance_agent.storage import open_db
from synthetic_lloyds import generate_histories, render_export

WriteExport = Callable[[str], Path]
LEAVES = [(top, sub) for top, subs in categorise.TAXONOMY.items() for sub in subs]


def bare(txns: list[Transaction]) -> list[Transaction]:
    """The Transactions themselves, without the categorisation overlay."""
    return [replace(t, categorisation=None) for t in txns]


def imported(conn: Connection, path: Path, user_id: int = 1) -> Ledger:
    ledger = Ledger(conn, user_id)
    ledger.import_files([path])
    categorise.run(conn, user_id)
    return ledger


@contextmanager
def seeded(seed: int, days: int) -> Generator[tuple[Connection, Path]]:
    current, _ = generate_histories(seed, days)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "export.csv"
        p.write_text(render_export(current, 0, len(current.txns)).text)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            yield conn, p
        engine.dispose()


def test_a_correction_recategorises_one_transaction(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        target = next(
            t
            for t in ledger.transactions()
            if t.categorisation and t.categorisation.kind == "Spending"
        )
        version = ledger.ledger_version()

        correction = ledger.correct_transaction(target.id, ("Travel", "Hotels"))

        after = {t.id: t for t in ledger.transactions()}
        c = after[target.id].categorisation
        assert c is not None
        assert (c.category, c.source) == (("Travel", "Hotels"), "correction")
        assert ledger.ledger_version() == version + 1
        assert correction.created_at is not None
        assert correction.reverted_at is None


def categorised(ledger: Ledger) -> list[Transaction]:
    return [
        t
        for t in ledger.transactions()
        if t.categorisation and t.categorisation.kind in ("Spending", "Refund")
    ]


def category_of(ledger: Ledger, transaction_id: int) -> tuple[str, str] | None:
    c = {t.id: t.categorisation for t in ledger.transactions()}[transaction_id]
    assert c is not None
    return c.category


def merchant_of(t: Transaction) -> int:
    assert t.categorisation is not None and t.categorisation.merchant_id is not None
    return t.categorisation.merchant_id


@settings(max_examples=20, deadline=None)
@given(st.integers(0, 2**32 - 1), st.integers(5, 40), st.data())
def test_a_correction_beats_every_rule_and_never_changes_the_transaction(
    seed: int, days: int, data: st.DataObject
) -> None:
    with seeded(seed, days) as (conn, path):
        ledger = imported(conn, path)
        candidates = categorised(ledger)
        if not candidates:
            return
        before = bare(ledger.transactions())
        by_txn = data.draw(
            st.dictionaries(
                st.sampled_from([t.id for t in candidates]),
                st.sampled_from(LEAVES),
                max_size=5,
            )
        )
        merchants = sorted(
            {merchant_of(t) for t in candidates if t.categorisation.merchant_id}  # type: ignore[union-attr]
        )
        by_merchant: dict[int, tuple[str, str]] = (
            data.draw(
                st.dictionaries(
                    st.sampled_from(merchants), st.sampled_from(LEAVES), max_size=3
                )
            )
            if merchants
            else {}
        )
        version = ledger.ledger_version()
        for m, cat in by_merchant.items():
            ledger.correct_merchant(m, cat)
        for t, cat in by_txn.items():
            ledger.correct_transaction(t, cat)
        assert ledger.ledger_version() == version + len(by_txn) + len(by_merchant)

        def check() -> None:
            assert bare(ledger.transactions()) == before  # Transactions untouched
            for t in categorised(ledger):
                c = t.categorisation
                assert c is not None
                want = by_txn.get(t.id) or by_merchant.get(c.merchant_id or -1)
                if want:
                    assert (c.category, c.source) == (want, "correction")
                else:
                    assert c.source != "correction"

        check()
        categorise.run(conn, 1)  # rules re-run
        check()
        ledger.import_files([path])  # the same file again
        categorise.run(conn, 1)
        check()


def test_a_transaction_correction_beats_its_merchants_correction(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=60)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 100).text))
        tesco = [
            t
            for t in categorised(ledger)
            if t.categorisation and t.categorisation.merchant == "Tesco"
        ]
        assert len(tesco) >= 2
        ledger.correct_merchant(merchant_of(tesco[0]), ("Shopping", "Household"))
        ledger.correct_transaction(tesco[0].id, ("Entertainment", "Events"))
        assert category_of(ledger, tesco[0].id) == ("Entertainment", "Events")
        for t in tesco[1:]:
            assert category_of(ledger, t.id) == ("Shopping", "Household")
        month = tesco[1].posted_on.strftime("%Y-%m")
        assert "Shopping" in {s.name for s in ledger.spending_by_category(month)}


def test_corrections_are_timestamped_and_reversible(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        t = categorised(ledger)[0]
        first = ledger.correct_transaction(t.id, ("Travel", "Hotels"))
        second = ledger.correct_transaction(t.id, ("Travel", "Flights"))
        assert category_of(ledger, t.id) == ("Travel", "Flights")

        version = ledger.ledger_version()
        ledger.revert_correction(second.id)
        assert ledger.ledger_version() == version + 1
        assert category_of(ledger, t.id) == ("Travel", "Hotels")
        ledger.revert_correction(first.id)
        after = {x.id: x.categorisation for x in ledger.transactions()}
        assert after[t.id] == t.categorisation

        history = ledger.corrections()  # the audit trail keeps both
        assert [k.id for k in history] == [first.id, second.id]
        assert all(k.reverted_at and k.reverted_at >= k.created_at for k in history)
        with pytest.raises(ValueError):
            ledger.revert_correction(first.id)  # already reverted


def test_corrections_survive_a_taxonomy_version_and_rule_change(
    engine: Engine, write_export: WriteExport, monkeypatch: pytest.MonkeyPatch
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        t = categorised(ledger)[0]
        ledger.correct_transaction(t.id, ("Travel", "Hotels"))
        monkeypatch.setattr(categorise, "TAXONOMY_VERSION", 2)
        monkeypatch.setattr(categorise, "RULES", ())
        categorise.run(conn, 1)
        c = {x.id: x.categorisation for x in ledger.transactions()}[t.id]
        assert c is not None
        assert (c.category, c.source, c.taxonomy_version) == (
            ("Travel", "Hotels"),
            "correction",
            2,
        )


def test_corrections_are_scoped_to_their_user(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        path = write_export(render_export(current, 0, 50).text)
        alice, bob = imported(conn, path, 1), imported(conn, path, 2)
        a, b = categorised(alice)[0], categorised(bob)[0]
        bob_version = bob.ledger_version()
        with pytest.raises(ValueError):
            alice.correct_transaction(b.id, ("Travel", "Hotels"))
        with pytest.raises(ValueError):
            alice.correct_merchant(merchant_of(b), ("Travel", "Hotels"))
        k = alice.correct_transaction(a.id, ("Travel", "Hotels"))
        with pytest.raises(ValueError):
            bob.revert_correction(k.id)
        assert bob.corrections() == []
        assert bob.ledger_version() == bob_version
        assert all(
            t.categorisation is None or t.categorisation.source != "correction"
            for t in bob.transactions()
        )


@pytest.mark.parametrize(
    "category", [("Travel", "Nope"), ("Nope", "Hotels"), ("Food & Drink", "Hotels")]
)
def test_a_correction_must_name_a_current_category(
    engine: Engine,
    write_export: WriteExport,
    monkeypatch: pytest.MonkeyPatch,
    category: tuple[str, str],
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        target = categorised(ledger)[0].id
        version = ledger.ledger_version()
        with pytest.raises(ValueError):
            ledger.correct_transaction(target, category)
        # a Category retired by a newer taxonomy version is refused too
        taxonomy = {k: v for k, v in categorise.TAXONOMY.items() if k != "Travel"}
        monkeypatch.setattr(categorise, "TAXONOMY", taxonomy)
        monkeypatch.setattr(categorise, "TAXONOMY_VERSION", 2)
        categorise.run(conn, 1)
        with pytest.raises(ValueError):
            ledger.correct_transaction(target, ("Travel", "Hotels"))
        assert ledger.ledger_version() == version


def test_every_import_that_stores_a_file_bumps_ledger_version(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        assert ledger.ledger_version() == 0
        first = write_export(render_export(current, 0, 20).text)
        ledger.import_files([first])
        assert ledger.ledger_version() == 1
        ledger.import_files([write_export(render_export(current, 10, 30).text)])
        assert ledger.ledger_version() == 2
        ledger.import_files([first])  # nothing new stored
        assert ledger.ledger_version() == 2


# --- verifier: adversarial tests -------------------------------------------


def total(spend: list[Spend]) -> Decimal:
    return sum((s.amount for s in spend), Decimal(0))


@settings(max_examples=15, deadline=None)
@given(st.integers(0, 2**32 - 1), st.data())
def test_corrections_move_spending_between_categories_but_never_change_totals(
    seed: int, data: st.DataObject
) -> None:
    with seeded(seed, 40) as (conn, path):
        ledger = imported(conn, path)
        candidates = categorised(ledger)
        if not candidates:
            return
        months = {t.posted_on.strftime("%Y-%m") for t in candidates}
        before = {m: ledger.spending_by_category(m) for m in months}
        summaries = {m: ledger.summarise_month(m) for m in months}
        picks = data.draw(
            st.lists(
                st.tuples(st.sampled_from(candidates), st.sampled_from(LEAVES)),
                max_size=6,
            )
        )
        for t, cat in picks:
            ledger.correct_transaction(t.id, cat)
        for m in months:
            assert total(ledger.spending_by_category(m)) == total(before[m])
            assert ledger.summarise_month(m) == summaries[m]
        # each corrected Transaction's amount sits under its corrected Category
        final = {t.id: cat for t, cat in picks}
        for t in candidates:
            if t.id in final:
                assert category_of(ledger, t.id) == final[t.id]


def test_a_refund_correction_reduces_the_corrected_category(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=120)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 150).text))
        refund = next(
            (
                t
                for t in categorised(ledger)
                if t.categorisation and t.categorisation.kind == "Refund"
            ),
            None,
        )
        if refund is None:
            pytest.skip("no Refund in this synthetic history")
        month = refund.posted_on.strftime("%Y-%m")
        ledger.correct_transaction(refund.id, ("Travel", "Hotels"))
        travel = {s.name: s for s in ledger.spending_by_category(month)}["Travel"]
        hotels = {s.name: s for s in travel.children}["Hotels"]
        assert hotels.amount <= -refund.amount  # the Refund reduces Hotels (ADR 0004)


def test_a_correction_never_gives_a_categoryless_kind_a_category(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=120)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 150).text))
        transfer = next(
            (
                t
                for t in ledger.transactions()
                if t.categorisation
                and t.categorisation.kind in ("Transfer", "Income", "Fee")
            ),
            None,
        )
        if transfer is None:
            pytest.skip("no Transfer, Income or Fee in this synthetic history")
        month = transfer.posted_on.strftime("%Y-%m")
        spend, summary = (
            ledger.spending_by_category(month),
            ledger.summarise_month(month),
        )
        ledger.correct_transaction(transfer.id, ("Travel", "Hotels"))
        c = {t.id: t.categorisation for t in ledger.transactions()}[transfer.id]
        assert c is not None and c.category is None and c.source != "correction"
        assert ledger.spending_by_category(month) == spend
        assert ledger.summarise_month(month) == summary


@pytest.mark.parametrize("order", ["merchant_first", "transaction_first"])
def test_reverting_merchant_and_transaction_corrections_in_any_order(
    engine: Engine, write_export: WriteExport, order: str
) -> None:
    current, _ = generate_histories(seed=7, days=60)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 100).text))
        tesco = [
            t
            for t in categorised(ledger)
            if t.categorisation and t.categorisation.merchant == "Tesco"
        ]
        one, other = tesco[0], tesco[1]
        rule = {t.id: t.categorisation for t in ledger.transactions()}
        by_m = ledger.correct_merchant(merchant_of(one), ("Shopping", "Household"))
        by_t = ledger.correct_transaction(one.id, ("Entertainment", "Events"))
        if order == "merchant_first":
            ledger.revert_correction(by_m.id)
            assert category_of(ledger, one.id) == ("Entertainment", "Events")
            assert category_of(ledger, other.id) == rule[other.id].category  # type: ignore[union-attr]
            ledger.revert_correction(by_t.id)
        else:
            ledger.revert_correction(by_t.id)
            assert category_of(ledger, one.id) == ("Shopping", "Household")
            assert category_of(ledger, other.id) == ("Shopping", "Household")
            ledger.revert_correction(by_m.id)
        now = {t.id: t.categorisation for t in ledger.transactions()}
        assert now[one.id] == rule[one.id] and now[other.id] == rule[other.id]


def test_many_corrections_on_one_transaction_last_wins_and_history_is_kept(
    engine: Engine, write_export: WriteExport
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        t = categorised(ledger)[0]
        version = ledger.ledger_version()
        made = [ledger.correct_transaction(t.id, cat) for cat in LEAVES[:10]]
        assert category_of(ledger, t.id) == LEAVES[9]
        ledger.revert_correction(made[4].id)  # reverting an older one changes nothing
        assert category_of(ledger, t.id) == LEAVES[9]
        ledger.revert_correction(made[9].id)  # the latest still-active one wins
        assert category_of(ledger, t.id) == LEAVES[8]
        assert [k.id for k in ledger.corrections()] == [k.id for k in made]
        assert ledger.ledger_version() == version + 12


@pytest.mark.parametrize("bad_id", [0, -1, 10**9])
def test_unknown_ids_are_refused_without_bumping_ledger_version(
    engine: Engine, write_export: WriteExport, bad_id: int
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        version = ledger.ledger_version()
        for call in (
            lambda: ledger.correct_transaction(bad_id, ("Travel", "Hotels")),
            lambda: ledger.correct_merchant(bad_id, ("Travel", "Hotels")),
            lambda: ledger.revert_correction(bad_id),
        ):
            with pytest.raises(ValueError):
                call()
        assert ledger.corrections() == []
        assert ledger.ledger_version() == version


def test_a_correction_to_a_since_retired_category_still_applies_and_reverts(
    engine: Engine, write_export: WriteExport, monkeypatch: pytest.MonkeyPatch
) -> None:
    current, _ = generate_histories(seed=7, days=30)
    with engine.begin() as conn:
        ledger = imported(conn, write_export(render_export(current, 0, 50).text))
        t = categorised(ledger)[0]
        k = ledger.correct_transaction(t.id, ("Travel", "Hotels"))
        taxonomy = {a: b for a, b in categorise.TAXONOMY.items() if a != "Travel"}
        monkeypatch.setattr(categorise, "TAXONOMY", taxonomy)
        monkeypatch.setattr(categorise, "TAXONOMY_VERSION", 2)
        categorise.run(conn, 1)
        # the user's choice is kept, never silently dropped, and stays reversible
        assert category_of(ledger, t.id) == ("Travel", "Hotels")
        ledger.revert_correction(k.id)
        c = {x.id: x.categorisation for x in ledger.transactions()}[t.id]
        assert c is not None and c.source != "correction"
        assert c.category is None or c.category[0] != "Travel"
