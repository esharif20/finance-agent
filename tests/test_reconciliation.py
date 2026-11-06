"""Reconciliation Gaps: the Ledger checked against the Bank's running balance."""

import dataclasses
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from finance_agent.ingest import lloyds
from finance_agent.ledger import Ledger
from synthetic_lloyds import MAX_ROWS, History, generate_histories, render_export
from test_dedupe import temp_ledger, windowed

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]


def export_rows(ledger: Ledger, d: Path, h: History, start: int, end: int) -> None:
    """Import rows [start, end) as one or more exports of at most MAX_ROWS."""
    paths: list[Path] = []
    for s in range(start, end, MAX_ROWS):
        p = d / f"rows{s}-{end}.csv"
        p.write_text(render_export(h, s, min(s + MAX_ROWS, end)).text)
        paths.append(p)
    ledger.import_files(paths)


@settings(max_examples=25, deadline=None)
@given(windowed())
def test_a_complete_history_has_no_gaps_and_sums_equal_the_balance_change(
    case: tuple[History, list[tuple[int, int]]],
) -> None:
    h, windows = case
    with temp_ledger() as (ledger, d):
        paths: list[Path] = []
        for i, (s, e) in enumerate(windows):
            p = d / f"export{i}.csv"
            p.write_text(render_export(h, s, e).text)
            paths.append(p)
        ledger.import_files(paths)

        assert ledger.gaps() == []
        # the Bank's own opening and closing balances are the truth
        opening = h.txns[0].balance_minor - h.txns[0].amount_minor
        change = Decimal(h.txns[-1].balance_minor - opening) / 100
        assert sum(t.amount for t in ledger.transactions()) == change


@st.composite
def dropped_window(draw: st.DrawFn) -> tuple[History, int, int]:
    """A history and an interior window [a, b) whose rows don't net to zero."""
    h = generate_histories(draw(st.integers(0, 2**32 - 1)), draw(st.integers(10, 150)))[
        0
    ]
    n = len(h.txns)
    a = draw(st.integers(1, n - 2))
    b = draw(st.integers(a + 1, n - 1))
    assume(sum(t.amount_minor for t in h.txns[a:b]) != 0)
    # ADR 0006 limit (see #6): a row whose full identity repeats in another file
    # is merged, so keep the three parts' identities apart
    parts = [{dataclasses.astuple(t) for t in p} for p in (h.txns[:a], h.txns[a:b])]
    later = {dataclasses.astuple(t) for t in h.txns[b:]}
    assume(not (parts[0] & parts[1] or parts[0] & later or parts[1] & later))
    return h, a, b


@settings(max_examples=25, deadline=None)
@given(dropped_window())
def test_dropping_a_window_opens_a_gap_and_reimporting_it_closes_it(
    case: tuple[History, int, int],
) -> None:
    h, a, b = case
    with temp_ledger() as (ledger, d):
        export_rows(ledger, d, h, 0, a)
        export_rows(ledger, d, h, b, len(h.txns))

        [gap] = ledger.gaps()
        assert gap.account.masked_number.endswith(h.account_number[-4:])
        assert gap.from_date == h.txns[a - 1].date
        assert gap.to_date == h.txns[b].date
        missing = sum(t.amount_minor for t in h.txns[a:b])
        assert gap.amount == Decimal(missing) / 100

        export_rows(ledger, d, h, a, b)

        assert ledger.gaps() == []


def test_a_break_in_the_banks_own_balance_is_a_gap_of_that_size(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=30)
    ledger = ledger_for(1)

    ledger.import_files(
        [write_export(render_export(h, 0, 40, gap_at=20, gap_minor=1234).text)]
    )

    [gap] = ledger.gaps()
    assert gap.amount == Decimal("12.34")
    assert (gap.from_date, gap.to_date) == (h.txns[19].date, h.txns[20].date)


def test_a_month_summary_lists_the_gaps_touching_it(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    i = next(i for i, t in enumerate(h.txns) if t.date.month == 2)
    ledger = ledger_for(1)

    ledger.import_files(
        [write_export(render_export(h, 0, i + 5, gap_at=i + 2, gap_minor=500).text)]
    )

    assert [g.amount for g in ledger.summarise_month("2026-02").gaps] == [
        Decimal("5.00")
    ]
    assert ledger.summarise_month("2026-03").gaps == []


# --- verifier: adversarial cases -------------------------------------------


def _missing(h: History, a: int, b: int) -> Decimal:
    return Decimal(sum(t.amount_minor for t in h.txns[a:b])) / 100


def test_the_first_row_has_no_anchor_until_earlier_rows_arrive(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=30)
    ledger = ledger_for(1)

    # a balance offset from the very first row is no break: nothing precedes it
    ledger.import_files(
        [write_export(render_export(h, 5, 20, gap_at=0, gap_minor=999).text)]
    )
    assert ledger.gaps() == []

    # earlier rows now anchor row 5; its printed balance is off by 9.99 plus
    # the two rows (3, 4) that are still missing
    ledger.import_files([write_export(render_export(h, 0, 3).text)])
    [gap] = ledger.gaps()
    assert (gap.from_date, gap.to_date) == (h.txns[2].date, h.txns[5].date)
    assert gap.amount == _missing(h, 3, 5) + Decimal("9.99")


def test_two_separate_gaps_in_one_account(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    assert _missing(h, 10, 15) != 0 and _missing(h, 30, 35) != 0
    ledger = ledger_for(1)

    for s, e in [(0, 10), (15, 30), (35, 50)]:
        ledger.import_files([write_export(render_export(h, s, e).text)])

    assert [(g.from_date, g.to_date, g.amount) for g in ledger.gaps()] == [
        (h.txns[9].date, h.txns[15].date, _missing(h, 10, 15)),
        (h.txns[29].date, h.txns[35].date, _missing(h, 30, 35)),
    ]


def test_a_gap_across_a_month_boundary_touches_both_months(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    i = next(i for i, t in enumerate(h.txns) if t.date.month == 2)
    assert h.txns[i - 3].date.month == 1 and h.txns[i + 2].date.month == 2
    ledger = ledger_for(1)

    ledger.import_files(
        [
            write_export(render_export(h, 0, i - 2).text),
            write_export(render_export(h, i + 2, len(h.txns)).text),
        ]
    )

    [gap] = ledger.gaps()
    assert gap.amount == _missing(h, i - 2, i + 2)
    assert ledger.summarise_month("2026-01").gaps == [gap]
    assert ledger.summarise_month("2026-02").gaps == [gap]
    assert ledger.summarise_month("2026-03").gaps == []


def test_rows_imported_into_the_middle_split_then_close_a_gap(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    ledger = ledger_for(1)

    def imp(s: int, e: int) -> list[tuple[int, int, Decimal]]:
        ledger.import_files([write_export(render_export(h, s, e).text)])
        return [
            (g.from_date.toordinal(), g.to_date.toordinal(), g.amount)
            for g in ledger.gaps()
        ]

    def span(a: int, b: int) -> tuple[int, int, Decimal]:
        return (
            h.txns[a - 1].date.toordinal(),
            h.txns[b].date.toordinal(),
            _missing(h, a, b),
        )

    imp(0, 10)
    assert imp(30, 50) == [span(10, 30)]
    assert imp(15, 20) == [span(10, 15), span(20, 30)]
    assert imp(10, 15) == [span(20, 30)]
    assert imp(20, 30) == []


def test_a_running_balance_that_changes_sign(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    # shift every printed balance so it crosses zero mid-history (overdrawn)
    shift = -h.txns[30].balance_minor
    signs = {(t.balance_minor + shift) > 0 for t in h.txns[:60]}
    assert signs == {True, False}
    ledger = ledger_for(1)

    ledger.import_files(
        [
            write_export(render_export(h, 0, 25, gap_at=0, gap_minor=shift).text),
            write_export(render_export(h, 35, 60, gap_at=0, gap_minor=shift).text),
        ]
    )
    [gap] = ledger.gaps()
    assert gap.amount == _missing(h, 25, 35)

    ledger.import_files(
        [write_export(render_export(h, 25, 35, gap_at=0, gap_minor=shift).text)]
    )
    assert ledger.gaps() == []


def test_gaps_belong_to_one_user(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    alice, bob = ledger_for(1), ledger_for(2)

    alice.import_files(
        [
            write_export(render_export(h, 0, 10).text),
            write_export(render_export(h, 20, 40).text),
        ]
    )
    # Bob's import recomputes Bob's Gaps only, and his rows don't close Alice's
    bob.import_files([write_export(render_export(h, 0, 40).text)])
    assert bob.gaps() == []
    assert [g.amount for g in alice.gaps()] == [_missing(h, 10, 20)]
    assert bob.summarise_month("2026-01").gaps == []

    bob.import_files([write_export(render_export(h, 50, 60).text)])
    alice.import_files([write_export(render_export(h, 10, 20).text)])
    assert alice.gaps() == []
    assert [g.amount for g in bob.gaps()] == [_missing(h, 40, 50)]


def test_same_day_rows_keep_the_files_order_not_a_sort(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    """Rows on one day that a sort by (date, description, amount, id) would
    reorder still reconcile, because Bank order comes from the file."""
    h, _ = generate_histories(seed=8, days=60)
    days: dict[object, int] = {}
    for t in h.txns:
        days[t.date] = days.get(t.date, 0) + 1
    assert max(days.values()) >= 2  # the history has busy days
    ledger = ledger_for(1)

    # newest rows first: storage ids no longer follow Bank order
    for s in range(50, -1, -10):
        ledger.import_files([write_export(render_export(h, s, s + 12).text)])

    assert ledger.gaps() == []


def test_split_twins_from_issue_34_surface_as_a_gap(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    """#34 limit: the second twin is merged into the first. The Ledger then
    misses a real Transaction, and reconciliation catches it as a Gap of the
    twin's amount rather than hiding it."""
    h, _ = generate_histories(seed=21, days=60)  # rows 113/115 twins, 114 refund
    ledger = ledger_for(1)

    ledger.import_files(
        [
            write_export(render_export(h, 100, 115).text),
            write_export(render_export(h, 114, 130).text),
        ]
    )

    assert [g.amount for g in ledger.gaps()] == [
        Decimal(h.txns[115].amount_minor) / 100
    ]


def test_a_newest_first_export_reconciles_in_bank_order(
    ledger_for: LedgerFor, write_export: WriteExport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If Lloyds files list newest first, Bank order must still come from the
    file (reversed), not from its physical line numbers."""
    monkeypatch.setattr(lloyds, "OLDEST_FIRST", False)
    h, _ = generate_histories(seed=8, days=60)
    ledger = ledger_for(1)

    def newest_first(s: int, e: int) -> Path:
        lines = render_export(h, s, e).text.splitlines()
        return write_export("\n".join(lines[:2] + lines[:1:-1]) + "\n")

    for s in range(0, 60, 10):
        ledger.import_files([newest_first(s, s + 12)])

    assert ledger.gaps() == []


def test_accounts_report_bank_and_ledger_balances_that_differ_by_the_gaps(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=30)
    ledger = ledger_for(1)
    ledger.import_files(
        [write_export(render_export(h, 0, 40, gap_at=20, gap_minor=1234).text)]
    )

    [acct] = ledger.accounts()

    assert acct.masked_number.endswith(h.account_number[-4:])
    assert acct.bank_balance == Decimal(h.txns[39].balance_minor + 1234) / 100
    assert acct.bank_balance - acct.ledger_balance == Decimal("12.34")
    assert [g.amount for g in acct.gaps] == [Decimal("12.34")]


def test_a_complete_account_has_equal_balances_and_no_gaps(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=8, days=30)
    ledger = ledger_for(1)
    ledger.import_files([write_export(render_export(h, 0, 40).text)])

    [acct] = ledger.accounts()

    assert acct.bank_balance == acct.ledger_balance
    assert acct.gaps == []
