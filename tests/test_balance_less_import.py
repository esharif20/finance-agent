"""HSBC exports carry no running balance (#130): such rows store NULL, like synced
rows, and an Account with no balances never opens a Reconciliation Gap."""

import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ingest import parse
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_banks import hsbc_export, revolut_export


def _ledger_import(d: Path, *texts: str) -> tuple[Ledger, Engine]:
    engine = open_db(Path(d) / "ledger.db")
    paths: list[Path] = []
    for i, text in enumerate(texts):
        p = Path(d) / f"export{i}.csv"
        p.write_text(text)
        paths.append(p)
    conn = engine.connect()
    ledger = Ledger(conn, 1)
    ledger.import_files(paths)
    return ledger, engine


def test_hsbc_rows_carry_no_balance() -> None:
    assert {r.balance_minor for r in parse(hsbc_export(1, 5).encode()).rows} == {None}


def _drop(text: str, keep: set[int]) -> str:
    head, *rows = text.splitlines()
    return "\n".join([head] + [r for i, r in enumerate(rows) if i in keep]) + "\n"


@settings(max_examples=25, deadline=None)
@given(
    st.integers(0, 10_000),
    st.integers(1, 30),
    st.lists(st.sets(st.integers(0, 29), min_size=1), min_size=1, max_size=4),
)
def test_no_import_of_balance_less_rows_ever_opens_a_gap(
    seed: int, n: int, windows: list[set[int]]
) -> None:
    full = hsbc_export(seed, n)
    # any non-empty subsets of the history, holes included
    files = [_drop(full, {i % n for i in keep}) for keep in windows]
    with tempfile.TemporaryDirectory() as d:
        ledger, engine = _ledger_import(Path(d), *files)
        assert ledger.gaps() == []
        for a in ledger.accounts():
            assert a.gaps == [] and a.bank_balance is None and a.ledger_balance is None
        engine.dispose()


def test_a_balance_less_account_is_listed_without_a_balance() -> None:
    with tempfile.TemporaryDirectory() as d:
        ledger, engine = _ledger_import(Path(d), hsbc_export(3, 4))
        (acc,) = ledger.accounts()
        assert acc.bank == "HSBC" and acc.bank_balance is None
        assert "all" in acc.name.lower()  # one Account for every HSBC file
        engine.dispose()


def test_twins_without_a_balance_are_both_kept_and_reimport_adds_nothing() -> None:
    text = "# synthetic\n01/01/2026,PRET 0231,-2.50\n01/01/2026,PRET 0231,-2.50\n"
    with tempfile.TemporaryDirectory() as d:
        ledger, engine = _ledger_import(Path(d), text, text + "\n")
        assert len(ledger.transactions()) == 2
        engine.dispose()


def test_revolut_still_reconciles_a_missing_row_is_a_gap_of_its_amount() -> None:
    full = revolut_export(7, 6)
    lines = full.splitlines()
    missing = parse(full.encode()).rows[3]
    holed = "\n".join(lines[:5] + lines[6:]) + "\n"  # drop data row 3 (line 6)
    with tempfile.TemporaryDirectory() as d:
        ledger, engine = _ledger_import(Path(d), holed, hsbc_export(1, 3))
        (gap,) = ledger.gaps()
        assert gap.amount == Decimal(missing.amount_minor).scaleb(-2)
        revolut = next(a for a in ledger.accounts() if a.bank == "Revolut")
        assert revolut.bank_balance == Decimal(
            parse(full.encode()).rows[-1].balance_minor or 0
        ).scaleb(-2)
        engine.dispose()


def test_revolut_pending_and_reverted_rows_are_counted_not_imported() -> None:
    p = parse(revolut_export(1, 3, state="PENDING").encode())
    assert p.rows == [] and p.skipped == 3
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        f = Path(d) / "r.csv"
        f.write_text(
            revolut_export(2, 2)
            + revolut_export(3, 2, state="REVERTED").split("\n", 2)[2]
        )  # COMPLETED rows then REVERTED rows
        with engine.begin() as conn:
            r = Ledger(conn, 1).import_files([f])
        assert (r.added, r.skipped) == (2, 2)
        engine.dispose()


def test_revolut_foreign_currency_refuses_the_file_and_says_fx_is_coming() -> None:
    text = revolut_export(1, 2) + revolut_export(2, 1, currency="EUR").split("\n", 2)[2]
    p = parse(text.encode())
    assert len(p.rejected) == 1 and "#156" in p.rejected[0].reason
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        f = Path(d) / "r.csv"
        f.write_text(text)
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            r = ledger.import_files([f])
            assert r.added == 0 and ledger.transactions() == []  # atomic, as Lloyds
        engine.dispose()


def test_api_shows_no_figure_for_a_balance_less_account(engine: Engine) -> None:
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "h.csv"
        f.write_text(hsbc_export(5, 3))
        with engine.begin() as conn:
            Ledger(conn, 1).import_files([f])
    with TestClient(api.create_app(engine)) as c:
        (acc,) = c.get("/api/accounts").json()
    assert acc["bank_balance"] is None and acc["ledger_balance"] is None
    assert acc["gaps"] == []


def test_fa_import_reports_skipped_rows_as_a_count_without_amounts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finance_agent.cli import main

    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    f = tmp_path / "r.csv"
    f.write_text(revolut_export(4, 3, state="PENDING", desc="SECRET SHOP"))
    assert main(["import", str(f)]) == 0
    out = capsys.readouterr().out
    assert "3 pending or reverted rows skipped" in out
    assert "SECRET" not in out and "." not in out.replace("..", "")
