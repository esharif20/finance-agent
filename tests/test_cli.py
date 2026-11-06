from decimal import Decimal
from pathlib import Path

import pytest

from finance_agent.cli import main
from synthetic_lloyds import generate_histories, render_export


def test_fa_import_then_summary_prints_the_figures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    h, _ = generate_histories(seed=3, days=60)
    csv = tmp_path / "lloyds.csv"
    csv.write_text(render_export(h, 0, 120).text)
    feb = [t.amount_minor for t in h.txns[:120] if t.date.month == 2]
    ins = Decimal(sum(a for a in feb if a > 0)) / 100
    outs = Decimal(sum(-a for a in feb if a < 0)) / 100

    assert main(["import", str(csv)]) == 0
    out = capsys.readouterr().out
    assert "120 added" in out
    assert "00000001" not in out

    assert main(["summary", "2026-02"]) == 0
    out = capsys.readouterr().out
    assert f"Money in   £{ins:,.2f}" in out
    assert f"Money out  £{outs:,.2f}" in out
    net = ins - outs
    assert f"Net        {'-' if net < 0 else ''}£{abs(net):,.2f}" in out


def test_money_format() -> None:
    from finance_agent.cli import gbp

    assert gbp(Decimal("-1234.5")) == "-£1,234.50"
    assert gbp(Decimal("0")) == "£0.00"


def test_bad_month_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    assert main(["summary", "2026-13"]) == 2
    assert "YYYY-MM" in capsys.readouterr().err


def test_refuses_a_database_inside_the_repo(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(Path(__file__).parent / "ledger.db"))
    assert main(["summary", "2026-01"]) == 1
    assert "inside a repository" in capsys.readouterr().err
