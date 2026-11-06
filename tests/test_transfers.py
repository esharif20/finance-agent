"""Transfers between the user's own Lloyds Accounts (ADR 0004, GLOSSARY)."""

import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import MAX_ROWS, History, Txn, generate_histories, render_export

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]


def month(t: Txn) -> str:
    return t.date.strftime("%Y-%m")


@settings(max_examples=15, deadline=None)
@given(st.integers(0, 2**32 - 1), st.integers(10, 70))
def test_transfer_pairs_net_to_zero_and_leave_money_in_and_out(
    seed: int, days: int
) -> None:
    current, saver = generate_histories(seed, days)
    current = replace(current, txns=current.txns[:MAX_ROWS])
    with tempfile.TemporaryDirectory() as d:
        paths: list[Path] = []
        for i, h in enumerate((saver, current)):
            p = Path(d) / f"export{i}.csv"
            p.write_text(render_export(h, 0, len(h.txns)).text)
            paths.append(p)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            ledger.import_files(paths)
            stored = {t.id: t for t in ledger.transactions()}
            linked = [t for t in stored.values() if t.transfer_with is not None]
            for t in linked:
                assert t.transfer_with is not None
                other = stored[t.transfer_with]
                assert other.transfer_with == t.id
                assert t.amount + other.amount == 0
            # the generator's TFR rows are exactly the own-account Transfers;
            # saver legs whose current leg was cut off by MAX_ROWS stay unpaired
            legs = {(t.date, -t.amount_minor) for t in current.txns if t.type == "TFR"}
            transfers = {
                (h.account_number, t)
                for h in (current, saver)
                for t in h.txns
                if t.type == "TFR"
                and ((t.date, t.amount_minor) in legs or h is current)
            }
            assert len(linked) == len(transfers)
            for m in sorted({month(t) for t in current.txns + saver.txns}):
                rows = [
                    t.amount_minor
                    for h in (current, saver)
                    for t in h.txns
                    if month(t) == m and (h.account_number, t) not in transfers
                ]
                s = ledger.summarise_month(m)
                assert s.money_in == Decimal(sum(a for a in rows if a > 0)) / 100
                assert s.money_out == Decimal(-sum(a for a in rows if a < 0)) / 100
        engine.dispose()


def _history(txns: list[tuple[int, str, str, int]], number: str) -> History:
    from synthetic_lloyds.generator import SORT_CODE, START

    out: list[Txn] = []
    bal = 100000
    for day, typ, desc, amt in txns:
        bal += amt
        out.append(Txn(START + timedelta(days=day), typ, desc, amt, bal))
    return History(SORT_CODE, number, out)


def test_only_own_account_tfr_rows_with_close_dates_are_linked(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    a = _history(
        [
            (0, "TFR", "SAVINGS", -5000),  # pairs with b's day-1 leg
            (0, "DEB", "TESCO STORES 3021", -2500),  # same amount as a credit in b
            (3, "TFR", "TO A FRIEND", -7000),  # no leg in b: not a Transfer
            (10, "TFR", "SAVINGS", -9000),  # b's leg is 6 days later: too far
        ],
        "00000001",
    )
    b = _history(
        [
            (1, "TFR", "SAVINGS", 5000),
            (1, "FPI", "ACME LTD SALARY", 2500),
            (16, "TFR", "SAVINGS", 9000),
        ],
        "00000002",
    )
    ledger = ledger_for(1)
    ledger.import_files([write_export(render_export(h, 0, 9).text) for h in (a, b)])

    linked = {
        (t.description, t.amount)
        for t in ledger.transactions()
        if t.transfer_with is not None
    }
    assert linked == {("SAVINGS", Decimal("-50.00")), ("SAVINGS", Decimal("50.00"))}


def test_a_leg_imported_later_still_pairs_and_identical_descriptions_win(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    a = _history([(0, "TFR", "MOVE 1", -1000)], "00000001")
    b = _history(
        [(0, "TFR", "SOMETHING ELSE", 1000), (1, "TFR", "MOVE 1", 1000)], "00000002"
    )
    ledger = ledger_for(1)
    ledger.import_files([write_export(render_export(a, 0, 9).text)])
    assert all(t.transfer_with is None for t in ledger.transactions())

    ledger.import_files([write_export(render_export(b, 0, 9).text)])

    by_id = {t.id: t for t in ledger.transactions()}
    pairs = {
        (t.description, by_id[t.transfer_with].description)
        for t in by_id.values()
        if t.transfer_with is not None and t.amount < 0
    }
    assert pairs == {("MOVE 1", "MOVE 1")}
