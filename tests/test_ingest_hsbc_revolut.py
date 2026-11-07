import gzip
import tempfile
import time
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent import ingest
from finance_agent.ingest import UnknownFormat, UnreadableFile, parse
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_banks import REVOLUT_HEADER, hsbc_export, revolut_export

LLOYDS_HEADER = (
    "Transaction Date,Transaction Type,Sort Code,Account Number,"
    "Transaction Description,Debit Amount,Credit Amount,Balance"
)
FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def test_fixtures_parse_as_their_bank() -> None:
    assert parse((FIXTURES / "hsbc_uk.csv").read_bytes()).bank == "HSBC"
    assert parse((FIXTURES / "revolut.csv").read_bytes()).bank == "Revolut"


def test_hsbc_rows_are_oldest_first_and_signed() -> None:
    text = "# synthetic\n02/01/2026,B SHOP,-1.50\n01/01/2026,SALARY,1000.00\n"
    p = parse(text.encode())
    assert [(r.description, r.amount_minor) for r in p.rows] == [
        ("SALARY", 100000),
        ("B SHOP", -150),
    ]
    assert p.rejected == []


def test_revolut_fee_is_folded_into_the_row_and_keeps_the_balance_chain() -> None:
    p = parse(revolut_export(3, 30).encode())
    assert p.rejected == []
    for a, b in zip(p.rows, p.rows[1:], strict=False):
        assert b.balance_minor is not None
        assert b.balance_minor - b.amount_minor == a.balance_minor


def test_revolut_foreign_rows_are_kept_in_their_own_currency_and_account() -> None:
    """#156: one Account per Product and currency (the Balance column runs per
    currency); a GBP Account keeps the ref it had before FX."""
    gbp = parse(revolut_export(1, 2).encode()).rows
    eur = parse(revolut_export(1, 2, currency="EUR").encode())
    assert eur.rejected == []
    assert {r.currency for r in eur.rows} == {"EUR"}
    assert [r.amount_minor for r in eur.rows] == [r.amount_minor for r in gbp]
    assert eur.rows[0].account_ref != gbp[0].account_ref
    assert eur.rows[0].masked_number != gbp[0].masked_number


@pytest.mark.parametrize("cur", ["eur", "EURO", "E1R", "", "€"])
def test_revolut_a_malformed_currency_is_rejected(cur: str) -> None:
    p = parse(revolut_export(1, 1, currency=cur).encode())
    assert [r.reason for r in p.rejected] == ["invalid currency"]


@pytest.mark.parametrize("state", ["PENDING", "REVERTED"])
def test_revolut_pending_and_reverted_are_not_imported(state: str) -> None:
    p = parse(revolut_export(1, 3, state=state).encode())
    assert p.rows == [] and p.rejected == []


def test_revolut_unknown_state_is_rejected() -> None:
    assert parse(revolut_export(1, 1, state="WEIRD").encode()).rejected


def test_detection_never_confuses_banks() -> None:
    for text, bank in [
        (hsbc_export(1), "HSBC"),
        (revolut_export(1), "Revolut"),
        (f"{LLOYDS_HEADER}\n", "Lloyds"),
    ]:
        assert [i.bank for i in ingest.IMPORTERS if i.detects(text)] == [bank]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "# synthetic\n",
        "Date,Description,Amount\n01/01/2026,X,1.00\n",
        "Type,Product,Started Date\n",
        REVOLUT_HEADER.replace("Fee", "Fees") + "\n",
        "01/01/2026,only,two,four\n",
        "2026-01-01,X,1.00\n",
        "01/01/2026,X,=1+1\n",
    ],
)
def test_unknown_layouts_raise(text: str) -> None:
    with pytest.raises(UnknownFormat):
        parse(text.encode())


def test_mixed_bank_headers_refuse_the_file() -> None:
    mixed = revolut_export(1, 2) + LLOYDS_HEADER + "\n"
    assert parse(mixed.encode()).rejected
    mixed = hsbc_export(1, 2) + REVOLUT_HEADER + "\n"
    assert parse(mixed.encode()).rejected


def test_formula_cells_are_kept_inert_and_never_become_money() -> None:
    p = parse(revolut_export(1, 1, desc='"=HYPERLINK(""http://x"",""y"")"').encode())
    assert p.rows[0].description.startswith("=HYPERLINK")
    bad = parse(b"# synthetic\n01/01/2026,Y,-1.00\n01/01/2026,X,=1+1\n")
    assert bad.bank == "HSBC" and bad.rejected  # amount cell is not a number
    bad = parse(
        b"# synthetic\n"
        + REVOLUT_HEADER.encode()
        + b"\nCARD,Current,2026-01-01 10:00:00,2026-01-01 10:00:00,"
        + b"X,@SUM(1),0,GBP,COMPLETED,1.00\n"
    )
    assert bad.rejected and bad.rows == []


def test_huge_file_parses_in_reasonable_time() -> None:
    text = revolut_export(5, 60_000)
    t = time.perf_counter()
    assert len(parse(text.encode()).rows) == 60_000
    assert time.perf_counter() - t < 20


def test_oversized_field_is_rejected_not_crashed() -> None:
    text = "# synthetic\n01/01/2026,X,1.00\n01/01/2026," + "A" * 200_000 + ",1.00\n"
    assert parse(text.encode()).rejected
    with pytest.raises(UnknownFormat):
        parse(("# synthetic\n01/01/2026," + "A" * 200_000 + ",1.00\n").encode())


def test_gzip_is_read_and_bombs_and_junk_are_refused() -> None:
    text = revolut_export(2, 5).encode()
    assert parse(gzip.compress(text)).rows == parse(text).rows
    with pytest.raises(UnreadableFile):
        parse(b"\x1f\x8b not gzip")
    with pytest.raises(UnreadableFile):
        parse(gzip.compress(text)[:-8])  # truncated
    old = ingest.MAX_BYTES
    ingest.MAX_BYTES = 1000
    try:
        with pytest.raises(UnreadableFile):
            parse(gzip.compress(text * 100))
    finally:
        ingest.MAX_BYTES = old


@settings(max_examples=15, deadline=None)
@given(st.integers(0, 10_000), st.integers(1, 40), st.sampled_from(["hsbc", "revolut"]))
def test_import_then_reimport_is_idempotent(seed: int, n: int, bank: str) -> None:
    text = hsbc_export(seed, n) if bank == "hsbc" else revolut_export(seed, n)
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "export.csv"
        f.write_text(text)
        g = Path(d) / "copy.csv"
        g.write_text(text + "\n")  # different bytes, same rows: not the same file hash
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            first = ledger.import_files([f])
            count = len(ledger.transactions())
            again = ledger.import_files([f, g])
            assert first.added == count == n
            assert again.added == 0
            assert len(ledger.transactions()) == count
        engine.dispose()


def test_concatenated_gzip_members_are_refused_not_silently_truncated() -> None:
    """Verifier (#162): a second gzip member must not be dropped without a word."""
    a, b = hsbc_export(1, 3), hsbc_export(2, 3).split("\n", 1)[1]
    with pytest.raises(UnreadableFile):
        parse(gzip.compress(a.encode()) + gzip.compress(b.encode()))
