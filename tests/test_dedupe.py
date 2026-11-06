"""Duplicate-free importing (ADR 0006), through the Ledger interface."""

import hashlib
import random
import tempfile
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import timedelta
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
from synthetic_lloyds.generator import SORT_CODE, START

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
    # seed 31 rows 69-71: SPOTIFY -10.99, REFUND +10.99, SPOTIFY -10.99 on one
    # day, so both purchases share date, description, amount and balance
    h, _ = generate_histories(seed=31, days=60)
    assert key(h.txns[69]) == key(h.txns[71])
    only_second = write_export(render_export(h, 70, 86).text)
    both = write_export(render_export(h, 56, 76).text)
    ledger = ledger_for(1)

    ledger.import_files([only_second, both])

    assert stored(ledger) == Counter(key(t) for t in h.txns[56:86])


# --- Verifier: multiset semantics over arbitrary (overlapping) imports ---------
# A tiny alphabet so identical keys collide often, inside and across files.
_ROW = st.builds(
    Txn,
    date=st.sampled_from([START, START + timedelta(days=1)]),
    type=st.just("DEB"),
    description=st.sampled_from(["SYNTH CAFE 01", "SYNTH SHOP 02"]),
    amount_minor=st.sampled_from([-450, 450]),
    balance_minor=st.sampled_from([10000, 9550]),
)
_FILES = st.lists(st.lists(_ROW, min_size=1, max_size=8), min_size=1, max_size=4)


def _csv(rows: list[Txn]) -> str:
    return render_export(History(SORT_CODE, "00000001", rows), 0, len(rows)).text


def _expected(files: list[list[Txn]]) -> Counter[Key]:
    want: Counter[Key] = Counter()
    for f in files:
        want |= Counter(key(t) for t in f)  # | is per-key max
    return want


@settings(max_examples=150, deadline=None)
@given(_FILES, st.data())
def test_stored_count_is_max_multiplicity_within_any_single_import(
    files: list[list[Txn]], data: st.DataObject
) -> None:
    order = data.draw(st.permutations(range(len(files))))
    order += data.draw(st.lists(st.sampled_from(range(len(files)))))  # some again
    with temp_ledger() as (ledger, d):
        paths: list[Path] = []
        for i, f in enumerate(files):
            p = d / f"f{i}.csv"
            p.write_text(_csv(f))
            paths.append(p)

        report = ledger.import_files([paths[i] for i in order])

        assert stored(ledger) == _expected(files)
        assert report.added == sum(_expected(files).values())


@settings(max_examples=60, deadline=None)
@given(_FILES, st.randoms(use_true_random=False))
def test_import_order_does_not_change_the_ledger(
    files: list[list[Txn]], rnd: random.Random
) -> None:
    shuffled = files[:]
    rnd.shuffle(shuffled)
    results: list[Counter[Key]] = []
    for fs in (files, shuffled):
        with temp_ledger() as (ledger, d):
            paths: list[Path] = []
            for i, f in enumerate(fs):
                p = d / f"f{i}.csv"
                p.write_text(_csv(f))
                paths.append(p)
            ledger.import_files(paths)
            results.append(stored(ledger))
    assert results[0] == results[1]


@settings(max_examples=60, deadline=None)
@given(_FILES)
def test_reimporting_any_file_is_a_no_op(files: list[list[Txn]]) -> None:
    with temp_ledger() as (ledger, d):
        paths: list[Path] = []
        for i, f in enumerate(files):
            p = d / f"f{i}.csv"
            p.write_text(_csv(f))
            paths.append(p)
        ledger.import_files(paths)
        before = ledger.transactions()

        for p in paths:
            assert ledger.import_files([p]).added == 0
        assert ledger.transactions() == before


@settings(max_examples=60, deadline=None)
@given(st.lists(_ROW, min_size=1, max_size=8), st.sampled_from(["\n", "\n\n", "crlf"]))
def test_same_rows_under_another_name_and_bytes_do_not_duplicate(
    rows: list[Txn], variant: str
) -> None:
    text = _csv(rows)
    other = text.replace("\n", "\r\n") if variant == "crlf" else text + variant
    with temp_ledger() as (ledger, d):
        a, b = d / "statement.csv", d / "statement (1).csv"
        a.write_text(text)
        b.write_bytes(other.encode())
        assert a.read_bytes() != b.read_bytes()

        ledger.import_files([a])
        before = stored(ledger)
        again = ledger.import_files([b])

        assert stored(ledger) == before == Counter(key(t) for t in rows)
        assert again.added == 0


@pytest.mark.xfail(
    strict=True,
    reason="ADR 0006 limit: twins split across files that overlap only between "
    "them are indistinguishable by key, so one is taken as already known",
)
def test_twins_split_across_files_by_a_one_row_overlap(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=31, days=60)  # rows 69/71 are twins, 70 refund
    first = write_export(render_export(h, 56, 71).text)
    second = write_export(render_export(h, 70, 86).text)
    ledger = ledger_for(1)

    ledger.import_files([first, second])

    assert stored(ledger) == Counter(key(t) for t in h.txns[56:86])
