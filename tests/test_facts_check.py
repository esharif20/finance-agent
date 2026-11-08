"""Facts checker against saved synthetic pages; nothing is ever fetched."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import facts_check as fc

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "tests/fixtures/synthetic/facts_pages"

TODAY = date(2026, 12, 1)


def run(
    tmp_path: Path,
    page: str | None,
    fact_id: str = "isa_allowance",
    value: int = 2000000,
) -> tuple[tuple[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]:
    p = tmp_path / "facts.json"
    p.write_text(
        json.dumps(
            {"facts": [{"id": fact_id, "value": value, "checked_on": "2026-11-08"}]}
        )
    )

    def fetcher(url: str) -> str | None:
        return (PAGES / page).read_text() if page else None

    return fc.check(p, fetcher, TODAY), json.loads(p.read_text())["facts"][0]


def test_unchanged(tmp_path: Path) -> None:
    (ch, fl), fact = run(tmp_path, "isa_unchanged.html")
    assert ch == [] and fl == [] and fact["value"] == 2000000


def test_changed(tmp_path: Path) -> None:
    (ch, fl), fact = run(tmp_path, "isa_changed.html")
    assert fl == [] and fact["value"] == 2500000
    assert ch[0]["old"] == 2000000 and ch[0]["new"] == 2500000
    assert ch[0]["source"].startswith("https://")


def test_layout_changed_reports_failure_and_keeps_value(tmp_path: Path) -> None:
    (ch, fl), fact = run(tmp_path, "isa_layout_changed.html")
    assert ch == [] and fl[0]["reason"] == "parser found no value"
    assert fact["value"] == 2000000


def test_unreachable(tmp_path: Path) -> None:
    (ch, fl), fact = run(tmp_path, None)
    assert ch == [] and fl[0]["reason"] == "unreachable" and fact["value"] == 2000000


def test_other_parsers(tmp_path: Path) -> None:
    for fid, page, val in [
        ("fscs_deposit_limit", "fscs_unchanged.html", 12000000),
        ("personal_allowance", "tax_unchanged.html", 1257000),
        ("student_loan_plan2_threshold", "sl_unchanged.html", 2847000),
    ]:
        (ch, fl), _ = run(tmp_path, page, fid, val)
        assert ch == [] and fl == [], fid
