"""Categorising stored Transactions and spending by Category (ADR 0004/0005)."""

import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.ledger import Ledger, Spend
from finance_agent.storage import open_db
from synthetic_lloyds import MAX_ROWS, History, Txn, generate_histories, render_export
from synthetic_lloyds.generator import SORT_CODE, START

WriteExport = Callable[[str], Path]
KINDS = {"Spending", "Income", "Transfer", "Refund", "Fee"}


def history(txns: list[tuple[int, str, str, int]], number: str = "00000001") -> History:
    out: list[Txn] = []
    bal = 100000
    for day, typ, desc, amt in txns:
        bal += amt
        out.append(Txn(START + timedelta(days=day), typ, desc, amt, bal))
    return History(SORT_CODE, number, out)


def test_every_transaction_gets_a_kind_and_spending_a_recorded_category(
    engine: Engine, write_export: WriteExport
) -> None:
    current, saver = generate_histories(seed=7, days=40)
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        ledger.import_files([write_export(render_export(current, 0, 100).text)])
        categorise.run(conn, 1)
        ledger.import_files([write_export(render_export(saver, 0, 9).text)])
        categorise.run(conn, 1)  # recomputes: earlier TFR legs are now Transfers

        txns = ledger.transactions()
        assert txns
        for t in txns:
            c = t.categorisation
            assert c is not None
            assert c.kind in KINDS
            assert c.taxonomy_version == categorise.TAXONOMY_VERSION
            assert (c.kind == "Transfer") == (t.transfer_with is not None)
            assert c.source.split(":")[0] in ("merchant", "rule", "default")
            if c.kind in ("Spending", "Refund"):
                assert c.category is not None
                assert (c.merchant is None) == (c.merchant_id is None)
            else:
                assert (c.category, c.merchant, c.merchant_id) == (None, None, None)
        assert {t.categorisation.kind for t in txns if t.categorisation} >= {
            "Spending",
            "Income",
            "Refund",
            "Transfer",
        }


def test_spending_by_category_drills_down_to_subcategory_and_merchant(
    engine: Engine, write_export: WriteExport
) -> None:
    h = history(
        [
            (0, "FPI", "ACME LTD SALARY", 200000),
            (1, "DEB", "TESCO STORES 3021", -1250),
            (2, "DEB", "TESCO EXP 0042", -750),
            (3, "DEB", "OAKLEY CAR PARKING 12", -800),
            (4, "DEB", "UNHEARD-OF SHOP 99", -300),
            (5, "DEB", "TESCO REFUND", 500),
            (6, "FEE", "ARRANGED OVERDRAFT FEE", -1000),
            (40, "DEB", "TESCO STORES 3021", -9999),  # next month
        ]
    )
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        ledger.import_files([write_export(render_export(h, 0, 9).text)])
        categorise.run(conn, 1)

        got = ledger.spending_by_category("2026-01")

    d = Decimal
    assert got == [
        Spend(
            "Food & Drink",
            d("15.00"),
            (Spend("Groceries", d("15.00"), (Spend("Tesco", d("15.00")),)),),
        ),
        Spend(
            "Transport",
            d("8.00"),
            (Spend("Parking", d("8.00"), (Spend("OAKLEY CAR PARKING", d("8.00")),)),),
        ),
        Spend(
            "Uncategorised",
            d("3.00"),
            (
                Spend(
                    "Uncategorised",
                    d("3.00"),
                    (Spend("UNHEARD-OF SHOP", d("3.00")),),
                ),
            ),
        ),
    ]


def merchant_ids(ledger: Ledger) -> dict[str, int | None]:
    return {
        t.description: t.categorisation.merchant_id
        for t in ledger.transactions()
        if t.categorisation
    }


def test_merchants_are_stable_rows_with_a_default_category(
    engine: Engine, write_export: WriteExport, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        (1, "DEB", "TESCO STORES 3021", -1250),
        (2, "DEB", "OAKLEY CAR PARKING 12", -800),
        (3, "DEB", "TESCO EXP 0042", -750),
    ]
    h = history(rows)
    later = history([*rows, (9, "DEB", "OAKLEY CAR PARKING 77 LONDON", -400)])
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        ledger.import_files([write_export(render_export(h, 0, 9).text)])
        categorise.run(conn, 1)
        first = merchant_ids(ledger)

        monkeypatch.setattr(categorise, "RULES", ())  # rules change
        ledger.import_files([write_export(render_export(later, 0, 9).text)])
        categorise.run(conn, 1)
        second = merchant_ids(ledger)

        merchants = {m.name: m for m in ledger.merchants()}

    assert first["TESCO STORES 3021"] == first["TESCO EXP 0042"]
    assert second.items() >= first.items()
    assert second["OAKLEY CAR PARKING 77 LONDON"] == first["OAKLEY CAR PARKING 12"]
    # the base Merchants are seeded when they appear (#215), not all up front
    assert set(merchants) == {"Tesco", "OAKLEY CAR PARKING"}
    assert merchants["Tesco"].default_category == ("Food & Drink", "Groceries")
    assert merchants["OAKLEY CAR PARKING"].default_category is None
    assert merchants["Tesco"].id == first["TESCO STORES 3021"]


def totals(ledger: Ledger, month: str) -> dict[str, Decimal]:
    return {s.name: s.amount for s in ledger.spending_by_category(month)}


@settings(max_examples=15, deadline=None)
@given(st.integers(0, 2**32 - 1), st.integers(10, 70), st.integers(1, 60))
def test_refunds_reduce_their_category_in_the_month_received(
    seed: int, days: int, later: int
) -> None:
    h, _ = generate_histories(seed, days)
    h = replace(h, txns=h.txns[: MAX_ROWS - 1])
    last = h.txns[-1]
    refund = Txn(
        last.date + timedelta(days=later),
        "DEB",
        "TESCO REFUND",
        2345,
        last.balance_minor + 2345,
    )
    with_refund = replace(h, txns=[*h.txns, refund])
    received = refund.date.strftime("%Y-%m")
    months = sorted({t.date.strftime("%Y-%m") for t in with_refund.txns})
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            for user, hist in ((1, h), (2, with_refund)):
                p = Path(d) / f"u{user}.csv"
                p.write_text(render_export(hist, 0, len(hist.txns)).text)
                Ledger(conn, user).import_files([p])
                categorise.run(conn, user)
            before, after = Ledger(conn, 1), Ledger(conn, 2)
            for m in months:
                b, a = totals(before, m), totals(after, m)
                if m != received:
                    assert a == b
                    continue
                food = b.pop("Food & Drink", Decimal(0)) - Decimal("23.45")
                assert a.pop("Food & Drink") == food  # may go negative (ADR 0004)
                assert a == b
        engine.dispose()


def test_identity_ignores_kind_merchant_and_category(
    engine: Engine, write_export: WriteExport, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = generate_histories(seed=11, days=40)
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        ledger.import_files([write_export(render_export(h, 0, 60).text)])
        categorise.run(conn, 1)
        monkeypatch.setattr(categorise, "MERCHANTS", {})  # rules change
        categorise.run(conn, 1)

        again = ledger.import_files([write_export(render_export(h, 10, 70).text)])

        assert (again.added, again.already_known) == (10, 50)


def test_a_refund_in_a_later_month_reduces_that_month_and_can_go_negative(
    engine: Engine, write_export: WriteExport
) -> None:
    # ADR 0004: January keeps the purchase; February's Groceries go negative
    h = history(
        [
            (5, "DEB", "TESCO STORES 3021", -2000),
            (35, "DEB", "TESCO STORES 3021", -500),
            (40, "DEB", "TESCO REFUND", 2000),
        ]
    )
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        ledger.import_files([write_export(render_export(h, 0, 9).text)])
        categorise.run(conn, 1)

        jan, feb = (ledger.spending_by_category(m) for m in ("2026-01", "2026-02"))
        assert [(s.name, s.amount) for s in jan] == [("Food & Drink", Decimal("20.00"))]
        assert [(s.name, s.amount) for s in feb] == [
            ("Food & Drink", Decimal("-15.00"))
        ]
        groceries = feb[0].children[0]
        assert (groceries.name, groceries.children) == (
            "Groceries",
            (Spend("Tesco", Decimal("-15.00")),),
        )


def test_two_users_merchants_never_mix(
    engine: Engine, write_export: WriteExport
) -> None:
    h = history(
        [(1, "DEB", "TESCO STORES 3021", -100), (2, "DEB", "CORNER SHOP", -200)]
    )
    with engine.begin() as conn:
        for user in (1, 2):
            Ledger(conn, user).import_files([write_export(render_export(h, 0, 9).text)])
            categorise.run(conn, user)
        one, two = (merchant_ids(Ledger(conn, u)) for u in (1, 2))
        ids = [{m.id for m in Ledger(conn, u).merchants()} for u in (1, 2)]
    assert set(one) == set(two)
    assert not set(one.values()) & set(two.values())
    assert not ids[0] & ids[1]
