"""Verifier tests (#5): awkward amounts, hostile CSVs, cross-user reads, bad paths."""

import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.cli import main
from finance_agent.ingest import parse
from finance_agent.ingest.lloyds import HEADER
from finance_agent.ledger import Ledger
from finance_agent.storage import UnsafeDatabasePath, open_db

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]

HEAD = "# synthetic\n" + ",".join(HEADER) + "\n"


def row(
    day: str, desc: str, debit: str = "", credit: str = "", bal: str = "0.00"
) -> str:
    return f"{day},DEB,11-22-33,00000001,{desc},{debit},{credit},{bal}\n"


# Two-decimal strings including leading zeros, x.10, x.01 and values past 2**53.
pence = st.integers(min_value=0, max_value=10**17).map(
    lambda p: f"{p // 100}.{p % 100:02d}"
)


@settings(max_examples=200, deadline=None)
@given(pence, st.booleans())
def test_parsed_amounts_are_exact_minor_units(text: str, out: bool) -> None:
    csv = HEAD + (
        row("01/01/2026", "X", debit=text)
        if out
        else row("01/01/2026", "X", credit=text)
    )
    [r] = parse(csv.encode()).rows
    assert isinstance(r.amount_minor, int)
    assert Decimal(r.amount_minor) == (-1 if out else 1) * Decimal(text) * 100


@pytest.mark.parametrize(
    "bad", ["1,234.56", "-0.00", "1.234", "1e3", "NaN", "0.1.0", " ", "£1.00"]
)
def test_awkward_amount_text_is_rejected_not_guessed(bad: str) -> None:
    csv = HEAD + f'01/01/2026,DEB,11-22-33,00000001,X,"{bad}",,0.00\n'
    parsed = parse(csv.encode())
    assert parsed.rows == [] and len(parsed.rejected) == 1


def test_summary_is_exact_for_tenths(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    # 0.10 + 0.20 is the classic float trap.
    csv = (
        HEAD
        + row("01/03/2026", "A", credit="0.10")
        + row("02/03/2026", "B", credit="0.20")
    )
    led = ledger_for(1)
    led.import_files([write_export(csv)])
    s = led.summarise_month("2026-03")
    assert s.money_in == Decimal("0.30") and type(s.money_in) is Decimal


def test_user_b_cannot_see_user_a_through_any_entry_point(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    f = write_export(HEAD + row("01/03/2026", "A", credit="5.00"))
    a, b = ledger_for(1), ledger_for(2)
    assert a.import_files([f]).added == 1
    # Same bytes for B is a fresh import, not "already known" from A's history.
    rb = b.import_files([f])
    assert (rb.added, rb.already_known) == (1, 0)
    assert ledger_for(3).summarise_month("2026-03").money_in == 0
    assert b.summarise_month("2026-03").money_in == Decimal("5.00")


def test_hostile_csv_cells(ledger_for: LedgerFor, write_export: WriteExport) -> None:
    csv = (
        HEAD
        + row("01/03/2026", '=HYPERLINK("http://x")', debit="1.00")
        + "01/03/2026,DEB,11-22-33,00000001,X,1.00,,0.00,extra\n"  # extra column
        + row(
            "01/03/2026", "PAY 12345678 30-00-00", credit="2.00"
        )  # account number in desc
    )
    r = ledger_for(1).import_files([write_export(csv)])
    assert r.added == 2 and [x.line for x in r.rejected] == [4]
    assert "12345678" not in repr(r) and "HYPERLINK" not in repr(r)


def test_header_only_file_imports_nothing(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    r = ledger_for(1).import_files([write_export(HEAD)])
    assert (r.added, r.rejected) == (0, [])


def test_non_utf8_file_fails_cleanly_without_leaking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db" / "ledger.db"))
    f = tmp_path / "latin1.csv"
    f.write_bytes(
        (HEAD + row("01/03/2026", "CAFÉ SECRETDESC", debit="3.00")).encode("latin-1")
    )
    assert main(["import", str(f)]) == 1
    err = capsys.readouterr().err
    assert "SECRETDESC" not in err and "3.00" not in err


def test_db_errors_do_not_leak_row_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An amount past SQLite int64 fails the insert; the error must not echo the row.
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db" / "ledger.db"))
    f = tmp_path / "huge.csv"
    f.write_text(
        HEAD + row("01/03/2026", "SECRETDESC", credit="99999999999999999999.99")
    )
    try:
        main(["import", str(f)])
    except BaseException as e:  # a crash is acceptable; a leaking message is not
        msg = str(e)
    else:
        msg = ""
    captured = capsys.readouterr()
    for text in (msg, captured.out, captured.err):
        assert "SECRETDESC" not in text and "9999999999" not in text


def test_db_path_into_repo_via_symlink_or_dotdot_is_refused(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parent
    link = tmp_path / "link"
    os.symlink(repo, link)
    with pytest.raises(UnsafeDatabasePath):
        open_db(link / "ledger.db")
    with pytest.raises(UnsafeDatabasePath):
        open_db(tmp_path / ".." / os.path.relpath(repo, tmp_path.parent) / "x.db")
    assert not (repo / "ledger.db").exists()
