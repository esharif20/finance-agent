from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]


def month_totals(month: str, amounts: list[tuple[str, int]]) -> tuple[int, int]:
    ins = sum(a for m, a in amounts if m == month and a > 0)
    outs = sum(-a for m, a in amounts if m == month and a < 0)
    return ins, outs


def test_import_then_summary_matches_the_export(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=3, days=60)
    path = write_export(render_export(h, 0, 120).text)
    ledger = ledger_for(1)

    report = ledger.import_files([path])

    assert report.added == 120
    assert report.rejected == []
    rows = [(t.date.strftime("%Y-%m"), t.amount_minor) for t in h.txns[:120]]
    ins, outs = month_totals("2026-01", rows)
    s = ledger.summarise_month("2026-01")
    assert s.money_in == Decimal(ins) / 100
    assert s.money_out == Decimal(outs) / 100
    assert s.net == Decimal(ins - outs) / 100
    assert s.currency == "GBP"
    assert all(type(v) is Decimal for v in (s.money_in, s.money_out, s.net))


def test_reimporting_an_identical_file_changes_nothing(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=5, days=40)
    path = write_export(render_export(h, 0, 50).text)
    ledger = ledger_for(1)
    ledger.import_files([path])
    before = ledger.summarise_month("2026-01")

    again = ledger.import_files([path])

    assert again.added == 0
    assert again.already_known == 50
    assert ledger.summarise_month("2026-01") == before


def test_malformed_row_rejects_the_file_with_its_line_number(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    h, _ = generate_histories(seed=9, days=30)
    export = render_export(h, 0, 20, malformed_after=7)
    path = write_export(export.text)

    report = ledger_for(1).import_files([path])

    assert report.added == 0  # the whole file is refused (#7)
    assert [(r.file, r.line) for r in report.rejected] == [
        (path.name, export.malformed_line)
    ]


def test_two_users_data_never_mix(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    a, _ = generate_histories(seed=11, days=25)
    b, _ = generate_histories(seed=12, days=25)
    shared = write_export(render_export(a, 0, 30).text)
    alice, bob = ledger_for(1), ledger_for(2)
    alice.import_files([shared])

    # Same bytes for Bob: his own Statement Import, not "already known".
    bob_shared = bob.import_files([shared])
    bob.import_files([write_export(render_export(b, 0, 30).text)])
    alice.import_files([write_export(render_export(a, 30, 40).text)])

    assert bob_shared.added == 30 and bob_shared.already_known == 0
    for ledger, rows in ((alice, a.txns[:40]), (bob, a.txns[:30] + b.txns[:30])):
        ins, outs = month_totals(
            "2026-01", [(t.date.strftime("%Y-%m"), t.amount_minor) for t in rows]
        )
        s = ledger.summarise_month("2026-01")
        assert (s.money_in, s.money_out) == (Decimal(ins) / 100, Decimal(outs) / 100)
    assert ledger_for(3).summarise_month("2026-01").net == 0


def test_empty_month_is_zero(ledger_for: LedgerFor) -> None:
    s = ledger_for(1).summarise_month("2026-12")
    assert (s.money_in, s.money_out, s.net) == (0, 0, 0)
