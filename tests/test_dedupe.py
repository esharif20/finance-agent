"""Duplicate-free importing (ADR 0006), through the Ledger interface."""

import hashlib
import random
import tempfile
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from finance_agent.cli import main
from finance_agent.ingest import ParsedRow
from finance_agent.ledger import Ledger, row_identity
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
    # seed 21 rows 113-115: SPOTIFY -10.99, REFUND +10.99, SPOTIFY -10.99 on one
    # day, so both purchases share date, description, amount and balance
    h, _ = generate_histories(seed=21, days=60)
    assert key(h.txns[113]) == key(h.txns[115])
    only_second = write_export(render_export(h, 114, 130).text)
    both = write_export(render_export(h, 100, 120).text)
    ledger = ledger_for(1)

    ledger.import_files([only_second, both])

    assert stored(ledger) == Counter(key(t) for t in h.txns[100:130])


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


@st.composite
def twin_histories(draw: st.DrawFn) -> History:
    """A generated history with full-key twins (same date, description, amount
    and balance) spliced in: purchase, refund, same purchase.

    Not purchase, refund twice over: with `P R P R P`, an export ending `P R P`
    and one starting `P R P x` fit with an overlap of one row or of three, and no
    method using the rows alone can tell which (ADR 0006).
    """
    base = generate_histories(
        draw(st.integers(0, 2**32 - 1)), draw(st.integers(10, 150))
    )[0]
    events = [(t.date, t.description, t.amount_minor) for t in base.txns]
    # splice in purchase, refund, same purchase: full-key twins (same balance)
    for at in sorted(
        draw(st.lists(st.integers(0, len(events) - 1), min_size=1, max_size=4)),
        reverse=True,
    ):
        d = events[at][0]
        events[at:at] = [
            (d, "SYNTH TWIN 01", -450),
            (d, "SYNTH TWIN REFUND", 450),
            (d, "SYNTH TWIN 01", -450),
        ]
    txns: list[Txn] = []
    bal = 100000
    for d, desc, amt in events:
        bal += amt
        txns.append(Txn(d, "DEB", desc, amt, bal))
    return History(SORT_CODE, "00000001", txns)


def _spans(n: int) -> st.SearchStrategy[list[tuple[int, int]]]:
    """Up to four contiguous windows [s, e) of n rows, anywhere."""
    return st.lists(
        st.tuples(st.integers(0, n - 1), st.integers(1, MAX_ROWS)).map(
            lambda p: (p[0], min(n, p[0] + p[1]))
        ),
        min_size=1,
        max_size=4,
    )


@settings(max_examples=150, deadline=None)
@given(twin_histories(), st.data())
def test_stored_count_is_max_multiplicity_within_any_single_import(
    h: History, data: st.DataObject
) -> None:
    # any contiguous windows, overlapping, abutting or apart, with no twin split
    # across files: then per-key max multiplicity is exactly the rows covered
    spans = data.draw(_spans(len(h.txns)))
    files = [h.txns[s:e] for s, e in spans]
    covered = {i for s, e in spans for i in range(s, e)}
    assume(Counter(key(h.txns[i]) for i in covered) == _expected(files))
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
@given(twin_histories(), st.data(), st.randoms(use_true_random=False))
def test_import_order_does_not_change_the_ledger(
    h: History, data: st.DataObject, rnd: random.Random
) -> None:
    # files are windows of one history: arbitrary row lists have no Bank order
    files = [h.txns[s:e] for s, e in data.draw(_spans(len(h.txns)))]
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


def test_twins_split_across_files_by_a_one_row_overlap(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=21, days=60)  # rows 113/115 are twins, 114 refund
    first = write_export(render_export(h, 100, 115).text)
    second = write_export(render_export(h, 114, 130).text)
    ledger = ledger_for(1)

    ledger.import_files([first, second])

    assert stored(ledger) == Counter(key(t) for t in h.txns[100:130])


# --- Sequence alignment (ADR 0006 amendment, #34) -----------------------------
@st.composite
def covering_windows(draw: st.DrawFn) -> tuple[History, list[tuple[int, int]]]:
    """Random contiguous windows that together cover a history, in random order.

    Neighbouring windows overlap by any amount, or just abut.
    """
    h = draw(twin_histories())
    txns = h.txns
    n = len(h.txns)
    # n // 15 bounds the file count for speed
    smallest = max(6, n // 15)
    after_refund = [
        i + 1 for i, t in enumerate(txns) if t.description == "SYNTH TWIN REFUND"
    ]
    windows: list[tuple[int, int]] = []
    s = 0
    while True:
        e = min(n, s + draw(st.integers(smallest, max(smallest, MAX_ROWS))))
        # often end just after a refund and start the next file at it, which
        # splits the twins around it across two files (#34)
        ends = [x for x in after_refund if s + smallest <= x <= s + MAX_ROWS]
        if ends and draw(st.booleans()):
            e = draw(st.sampled_from(ends))
        windows.append((s, e))
        if e == n:
            break
        s = draw(st.one_of(st.just(e - 1), st.integers(s + 1, e)))
    return h, draw(st.permutations(windows))


@settings(max_examples=40, deadline=None)
@given(covering_windows())
def test_random_covering_windows_with_repeated_keys_reproduce_the_history(
    case: tuple[History, list[tuple[int, int]]],
) -> None:
    h, windows = case
    assert max(Counter(key(t) for t in h.txns).values()) >= 2  # twins present
    with temp_ledger() as (ledger, d):
        paths: list[Path] = []
        for i, (s, e) in enumerate(windows):
            p = d / f"export{i}.csv"
            p.write_text(render_export(h, s, e).text)
            paths.append(p)

        report = ledger.import_files(paths)

        assert stored(ledger) == Counter(key(t) for t in h.txns)
        assert report.added == len(h.txns)


def test_a_row_that_could_be_either_twin_is_known_and_reported(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=21, days=60)  # rows 113/115 are twins, 114 refund
    ledger = ledger_for(1)
    ledger.import_files([write_export(render_export(h, 110, 120).text)])

    report = ledger.import_files([write_export(render_export(h, 115, 116).text)])

    assert (report.added, report.already_known, report.ambiguous) == (0, 1, 1)
    assert stored(ledger) == Counter(key(t) for t in h.txns[110:120])


_TEXT = st.text("ABC *0123456789", min_size=1, max_size=12)


@given(
    st.builds(
        ParsedRow,
        line=st.integers(1, 200),
        account_ref=_TEXT,
        masked_number=_TEXT,
        posted_on=st.dates(),
        bank_type=_TEXT,
        description=_TEXT,
        amount_minor=st.integers(-(10**9), 10**9),
        balance_minor=st.integers(-(10**9), 10**9),
        currency=st.sampled_from(["GBP", "EUR"]),
    ),
    st.integers(1, 200),
    _TEXT,
)
def test_identity_uses_only_the_raw_bank_fields(
    row: ParsedRow, line: int, other: str
) -> None:
    # Firefly III #12246: hashing derived values re-imported duplicates. Fields
    # outside (date, description, amount, balance) never change identity, and
    # identity is computed before any categorisation or Merchant normalising.
    moved = replace(row, line=line, bank_type=other, masked_number=other)

    assert row_identity(moved) == row_identity(row)
    assert row_identity(row) == (
        row.posted_on,
        row.description,
        row.amount_minor,
        row.balance_minor,
    )
