"""Duplicate-free importing (ADR 0006), through the Ledger interface."""

import hashlib
import tempfile
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.cli import main
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import (
    MAX_ROWS,
    History,
    Txn,
    generate_histories,
    histories,
    overlapping_windows,
    render_export,
)

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]
Key = tuple[str, str, Decimal, Decimal]


def key(t: Txn) -> Key:
    return (
        t.date.isoformat(),
        t.description,
        Decimal(t.amount_minor) / 100,
        Decimal(t.balance_minor) / 100,
    )


def stored(ledger: Ledger) -> Counter[Key]:
    return Counter(
        (t.posted_on.isoformat(), t.description, t.amount, t.running_balance)
        for t in ledger.transactions()
    )


@contextmanager
def temp_ledger() -> Generator[tuple[Ledger, Path]]:
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        try:
            with engine.begin() as conn:
                yield Ledger(conn, 1), Path(d)
        finally:
            engine.dispose()


@st.composite
def windowed(draw: st.DrawFn) -> tuple[History, list[tuple[int, int]]]:
    """A history and overlapping windows over it, in a random import order."""
    h = generate_histories(draw(st.integers(0, 2**32 - 1)), draw(st.integers(10, 150)))[
        0
    ]
    n = len(h.txns)
    size = draw(st.integers(5, min(MAX_ROWS, n)))
    # at most ~12 windows per example keeps the property fast; overlap still varies
    step = draw(st.integers(max(1, min(size - 1, n // 12)), max(1, size - 1)))
    return h, draw(st.permutations(overlapping_windows(n, size, step)))


@settings(max_examples=25, deadline=None)
@given(histories())
def test_reimporting_an_identical_file_is_a_no_op(h: History) -> None:
    with temp_ledger() as (ledger, d):
        export = d / "export.csv"
        export.write_text(render_export(h, 0, min(MAX_ROWS, len(h.txns))).text)
        ledger.import_files([export])
        before = ledger.transactions()

        again = ledger.import_files([export])

        assert again.added == 0
        assert ledger.transactions() == before


@settings(max_examples=25, deadline=None)
@given(windowed())
def test_overlapping_windows_store_exactly_the_original_transactions(
    case: tuple[History, list[tuple[int, int]]],
) -> None:
    h, windows = case
    with temp_ledger() as (ledger, d):
        paths: list[Path] = []
        for i, (s, e) in enumerate(windows):
            p = d / f"export{i}.csv"
            p.write_text(render_export(h, s, e).text)
            paths.append(p)

        report = ledger.import_files(paths)

        assert stored(ledger) == Counter(key(t) for t in h.txns)
        assert report.added == len(h.txns)


@settings(max_examples=25, deadline=None)
@given(histories())
def test_identical_same_day_purchases_are_kept(h: History) -> None:
    repeats = Counter((t.date, t.description, t.amount_minor) for t in h.txns)
    assert max(repeats.values()) >= 2  # generator guarantees one repeat
    with temp_ledger() as (ledger, d):
        export = d / "export.csv"
        export.write_text(render_export(h, 0, min(MAX_ROWS, len(h.txns))).text)

        ledger.import_files([export])

        got = Counter(
            (t.posted_on, t.description, t.amount) for t in ledger.transactions()
        )
        want = Counter(
            (t.date, t.description, Decimal(t.amount_minor) / 100)
            for t in h.txns[:MAX_ROWS]
        )
        assert got == want


def test_each_transaction_links_to_every_statement_import_it_appeared_in(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=21, days=40)
    first = write_export(render_export(h, 0, 30).text)
    second = write_export(render_export(h, 20, 50).text)
    digest = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (first, second)}
    ledger = ledger_for(1)

    ledger.import_files([first, second])

    by_key = {
        (t.posted_on.isoformat(), t.description, t.amount, t.running_balance): t
        for t in ledger.transactions()
    }
    for i, t in enumerate(h.txns[:50]):
        want: set[str] = {digest[first]} if i < 30 else set()
        if i >= 20:
            want.add(digest[second])
        assert by_key[key(t)].statement_imports == frozenset(want)


def test_fa_import_takes_several_files_in_one_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    write_export: WriteExport,
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db" / "ledger.db"))
    h, _ = generate_histories(seed=22, days=40)
    a = write_export(render_export(h, 0, 30).text)
    b = write_export(render_export(h, 20, 50).text)

    assert main(["import", str(a), str(b)]) == 0

    assert capsys.readouterr().out.startswith("50 added, 10 already known, 0 rejected")


def test_twins_with_the_same_balance_survive_any_import_order(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    # seed 21 rows 113-115: SPOTIFY -10.99, REFUND +10.99, SPOTIFY -10.99 on one
    # day, so both purchases share date, description, amount and balance
    h, _ = generate_histories(seed=21, days=60)
    assert key(h.txns[113]) == key(h.txns[115])
    only_second = write_export(render_export(h, 114, 130).text)
    both = write_export(render_export(h, 100, 120).text)
    ledger = ledger_for(1)

    ledger.import_files([only_second, both])

    assert stored(ledger) == Counter(key(t) for t in h.txns[100:130])
