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


def own(t: Txn) -> bool:
    return "SYNTH SAVER" in t.description or "SYNTH CURRENT" in t.description


def test_generator_has_a_faster_payment_own_account_pair() -> None:
    current, saver = generate_histories(0, 10)
    assert {t.type for t in current.txns if own(t)} >= {"FPO"}
    assert {t.type for t in saver.txns if own(t)} >= {"FPI"}


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
            # the generator's SYNTH rows (TFR and FPO/FPI) are exactly the
            # own-account Transfers; saver legs whose current leg was cut off by
            # MAX_ROWS stay unpaired
            legs = {(t.date, -t.amount_minor) for t in current.txns if own(t)}
            transfers = {
                (h.account_number, t)
                for h in (current, saver)
                for t in h.txns
                if own(t) and ((t.date, t.amount_minor) in legs or h is current)
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


def test_only_opposite_close_legs_in_other_accounts_are_linked(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    a = _history(
        [
            (0, "TFR", "SAVINGS", -5000),  # pairs with b's day-1 leg
            (0, "FPO", "SYNTH SAVER", -2500),  # Faster Payment own-account move
            (2, "DEB", "TESCO STORES 3021", -4400),  # a's own refund: same Account
            (2, "DEB", "TESCO REFUND", 4400),
            (3, "TFR", "TO A FRIEND", -7000),  # no leg in b: not a Transfer
            (10, "TFR", "SAVINGS", -9000),  # b's leg is 6 days later: too far
        ],
        "00000001",
    )
    b = _history(
        [
            (1, "TFR", "SAVINGS", 5000),
            (1, "FPI", "SYNTH CURRENT", 2500),
            (3, "TFR", "SAVINGS", -7000),  # same sign as a's leg: not a pair
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
    assert linked == {
        ("SAVINGS", Decimal("-50.00")),
        ("SAVINGS", Decimal("50.00")),
        ("SYNTH SAVER", Decimal("-25.00")),
        ("SYNTH CURRENT", Decimal("25.00")),
    }
    s = ledger.summarise_month("2026-01")
    assert s.money_in == Decimal("44.00") + Decimal("90.00")  # refund, late leg
    assert s.money_out == Decimal("44.00") + Decimal("70.00") * 2 + Decimal("90.00")


def test_three_equal_candidate_legs_pair_at_most_once_and_tfr_breaks_ties(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    a = _history([(0, "DEB", "CAFE", -3000), (1, "TFR", "MOVE", -3000)], "00000001")
    b = _history([(1, "TFR", "MOVE IN", 3000)], "00000002")
    c = _history([(1, "FPI", "SOMEONE", 3000)], "00000003")
    ledger = ledger_for(1)
    ledger.import_files([write_export(render_export(h, 0, 9).text) for h in (a, b, c)])
    by_id = {t.id: t for t in ledger.transactions()}
    pairs = {
        (t.description, by_id[t.transfer_with].description)
        for t in by_id.values()
        if t.transfer_with is not None and t.amount < 0
    }
    # TFR on both legs beats the earlier card payment for the TFR credit;
    # the card payment then takes the remaining same-amount credit
    assert pairs == {("MOVE", "MOVE IN"), ("CAFE", "SOMEONE")}
    partners = [t.transfer_with for t in by_id.values() if t.transfer_with]
    assert len(partners) == len(set(partners)) == 4


def test_another_users_account_is_never_a_transfer_leg(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    ledger_for(1).import_files(
        [
            write_export(
                render_export(_history([(0, "FPO", "X", -100)], "00000001"), 0, 9).text
            )
        ]
    )
    ledger_for(2).import_files(
        [
            write_export(
                render_export(_history([(0, "FPI", "X", 100)], "00000002"), 0, 9).text
            )
        ]
    )
    for user in (1, 2):
        assert all(t.transfer_with is None for t in ledger_for(user).transactions())


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
