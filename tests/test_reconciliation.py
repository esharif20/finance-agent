"""Reconciliation Gaps: the Ledger checked against the Bank's running balance."""

import dataclasses
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from hypothesis import assume, given, settings
from hypothesis import strategies as st

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
