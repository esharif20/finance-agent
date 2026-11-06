"""#27: synced Transactions through the Ledger interface (ADR 0014)."""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from finance_agent.ledger import Ledger
from synthetic_lloyds import History, generate_histories, render_export
from synthetic_simplefin import response
from test_dedupe import temp_ledger

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]

H = generate_histories(7, 40)[0]


def snapshot(led: Ledger) -> list[tuple[Any, ...]]:
    return [(t.posted_on, t.description, t.amount) for t in led.transactions()]


def edit(raw: bytes, **fields: Any) -> bytes:
    """Change the first row of the first account."""
    doc = json.loads(raw)
    doc["accounts"][0]["transactions"][0].update(fields)
    return json.dumps(doc).encode()


def test_posted_rows_enter_the_ledger_and_pending_rows_do_not(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    r = led.apply_sync(response(H, 0, 10, pending=2))
    assert (r.added, r.already_known) == (10, 0)
    assert len(r.accounts) == 1
    assert len(led.transactions()) == 10
    jan = led.summarise_month("2026-01")
    expected = sum(t.amount_minor for t in H.txns[:10])
    assert jan.net == Decimal(expected).scaleb(-2)


def test_a_known_id_never_changes_and_is_reported(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    raw = response(H, 0, 5)
    led.apply_sync(raw)
    before = snapshot(led)
    r = led.apply_sync(edit(raw, amount="-999.99", description="CHANGED"))
    assert (r.added, r.already_known, r.changed_known) == (0, 5, 1)
    assert snapshot(led) == before


def test_one_id_with_differing_rows_rejects_that_accounts_sync(
    ledger_for: LedgerFor,
) -> None:
    doc = json.loads(response(H, 0, 3))
    rows = doc["accounts"][0]["transactions"]
    rows.append(rows[0] | {"amount": "-1.23"})
    r = ledger_for(1).apply_sync(json.dumps(doc).encode())
    assert (r.added, r.rejected_accounts) == (0, 1)


def test_same_purchase_under_distinct_ids_is_kept_and_flagged(
    ledger_for: LedgerFor,
) -> None:
    doc = json.loads(response(H, 0, 1))
    row = doc["accounts"][0]["transactions"][0]
    doc["accounts"][0]["transactions"] += [row | {"id": "7"}, row | {"id": "007"}]
    r = ledger_for(1).apply_sync(json.dumps(doc).encode())
    assert (r.added, r.provider_duplicates) == (3, 3)


def test_first_sync_anchors_and_a_missing_row_opens_a_gap(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    assert led.apply_sync(response(H, 0, 10)).gaps == []
    lost = H.txns[14]
    assert lost.amount_minor != 0
    r = led.apply_sync(response(H, 8, 20, drop={14}))
    assert [g.amount for g in r.gaps] == [Decimal(lost.amount_minor).scaleb(-2)]
    assert led.sync_gaps() == r.gaps


def test_a_gap_equal_to_the_pending_sum_is_reported_as_pending(
    ledger_for: LedgerFor,
) -> None:
    # Spike #26: the feed cannot say whether balance includes pending rows.
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 10))
    r = led.apply_sync(response(H, 10, 20, pending=2, balance_offset=-200))
    assert (r.gaps, r.pending_explained) == ([], 1)
    r = led.apply_sync(response(H, 10, 20, pending=2, balance_offset=-201))
    assert [g.amount for g in r.gaps] == [Decimal("-2.01")]


def test_csv_history_before_the_anchor_never_opens_a_gap(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    led = ledger_for(1)
    led.import_files([write_export(render_export(H, 0, 30).text)])
    (csv_account,) = led.accounts()
    led.link_synced_account("ACC-1", csv_account.id)
    led.apply_sync(response(H, 20, 30))
    r = led.apply_sync(response(H, 25, 40))
    assert r.gaps == [] and led.possible_duplicates() == []
    assert len(led.transactions()) == 40


def test_ambiguous_rows_stay_separate_and_are_possible_duplicates(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    led = ledger_for(1)
    led.import_files([write_export(render_export(H, 0, 10).text)])
    (csv_account,) = led.accounts()
    led.link_synced_account("ACC-1", csv_account.id)
    doc = json.loads(response(H, 0, 10))
    row = doc["accounts"][0]["transactions"][0]
    doc["accounts"][0]["transactions"].append(row | {"id": "again"})
    led.apply_sync(json.dumps(doc).encode())
    assert len(led.transactions()) == 12  # 9 pairs merged; 1 CSV + 2 synced kept
    (group,) = led.possible_duplicates()
    assert len(group) == 3


# --- properties ---------------------------------------------------------------


@st.composite
def histories_and_split(draw: st.DrawFn) -> tuple[History, int, int, int]:
    h = generate_histories(draw(st.integers(0, 2**32 - 1)), draw(st.integers(5, 40)))[0]
    n = len(h.txns)
    a = draw(st.integers(1, n))
    b = draw(st.integers(0, a - 1))
    return h, a, b, n


@settings(max_examples=25, deadline=None)
@given(histories_and_split())
def test_syncing_twice_changes_nothing(case: tuple[History, int, int, int]) -> None:
    h, a, b, n = case
    with temp_ledger() as (led, _):
        led.apply_sync(response(h, 0, a))
        led.apply_sync(response(h, b, n))
        before = (snapshot(led), led.sync_gaps())
        again = led.apply_sync(response(h, b, n))
        assert again.added == 0
        led.apply_sync(response(h, 0, n))  # different bytes, same rows
        assert (snapshot(led), led.sync_gaps()) == before
        assert len(before[0]) == n and before[1] == []


@settings(max_examples=25, deadline=None)
@given(histories_and_split(), st.booleans())
def test_csv_and_sync_never_double_count_unambiguous_rows(
    case: tuple[History, int, int, int], csv_first: bool
) -> None:
    h, a, b, n = case  # CSV holds rows [0, a), the sync [b, n): overlap [b, a)
    with temp_ledger() as (led, d):
        csv = d / "export.csv"
        csv.write_text(render_export(h, 0, a).text)
        if csv_first:
            led.import_files([csv])
        led.apply_sync(response(h, b, n))
        if not csv_first:
            led.import_files([csv])
        (acc,) = [a for a in led.accounts() if a.masked_number == "••••0001"]
        led.link_synced_account("ACC-1", acc.id)  # merges the synced Account in
        assert len(led.accounts()) == 1
        assume(led.possible_duplicates() == [])
        assert len(led.transactions()) == n
        total = sum(t.amount for t in led.transactions())
        assert total == Decimal(sum(t.amount_minor for t in h.txns)).scaleb(-2)


@settings(max_examples=25, deadline=None)
@given(histories_and_split(), st.data())
def test_a_feed_gap_opens_a_reconciliation_gap(
    case: tuple[History, int, int, int], data: st.DataObject
) -> None:
    h, a, _, n = case
    assume(a < n)
    lost = data.draw(st.integers(a, n - 1))
    assume(h.txns[lost].amount_minor != 0)
    with temp_ledger() as (led, _):
        led.apply_sync(response(h, 0, a))
        r = led.apply_sync(response(h, a, n, drop={lost}))
        assert [g.amount for g in r.gaps] == [
            Decimal(h.txns[lost].amount_minor).scaleb(-2)
        ]
