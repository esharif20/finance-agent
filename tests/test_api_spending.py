"""GET /api/spending: the Overview's donut data, a thin adapter over the Ledger."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise, cli, identity
from finance_agent.ledger import Ledger
from synthetic_lloyds import History, Txn, generate_histories, render_export
from synthetic_lloyds.generator import SORT_CODE, START


def history(txns: list[tuple[int, str, str, int]]) -> History:
    out: list[Txn] = []
    bal = 100000
    for day, typ, desc, amt in txns:
        bal += amt
        out.append(Txn(START + timedelta(days=day), typ, desc, amt, bal))
    return History(SORT_CODE, "00000001", out)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


@pytest.fixture
def seeded(engine: Engine, write_export: Callable[[str], Path]) -> str:
    """A categorised synthetic history; returns its latest month."""
    h, _ = generate_histories(seed=8, days=60)
    with engine.begin() as conn:
        ledger = Ledger(conn, identity.current_user())
        ledger.import_files([write_export(render_export(h, 0, 120).text)])
        categorise.run(conn, identity.current_user())
        return ledger.transactions()[-1].posted_on.strftime("%Y-%m")


def test_breakdown_is_the_ledgers_categories_in_minor_units(
    client: TestClient, engine: Engine, seeded: str
) -> None:
    with engine.begin() as conn:
        want = list(Ledger(conn, 1).category_breakdown(seeded).categories)

    body = client.get("/api/spending", params={"month": seeded}).json()

    assert body["month"] == seeded
    assert [c["name"] for c in body["categories"]] == [s.name for s in want]
    assert [c["amount"] for c in body["categories"]] == [
        {"minor": int(s.amount * 100), "currency": "GBP"} for s in want
    ]
    parts = body["total"], body["refunds"], body["fees"]
    assert body["money_out"]["minor"] == sum(p["minor"] for p in parts)
    assert body["total"]["minor"] == (
        sum(c["amount"]["minor"] for c in body["categories"])
        + body["unmatched_refunds"]["minor"]
    )


def test_defaults_to_the_latest_month_and_validates_it(
    client: TestClient, seeded: str
) -> None:
    assert client.get("/api/spending").json()["month"] == seeded
    assert client.get("/api/spending", params={"month": "2026-13"}).status_code == 422
    assert client.get("/api/spending", params={"month": "26-01"}).status_code == 422


def test_is_404_before_any_import(client: TestClient) -> None:
    assert client.get("/api/spending").status_code == 404


def test_months_lists_months_with_transactions_newest_first(
    client: TestClient, engine: Engine, seeded: str
) -> None:
    with engine.begin() as conn:
        want = Ledger(conn, 1).months()

    body = client.get("/api/months").json()

    assert body == want
    assert body[0] == seeded
    assert body == sorted(body, reverse=True)


def test_figures_equal_fa_summary_and_reconcile_with_the_breakdown(
    client: TestClient,
    engine: Engine,
    seeded: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "open_db", lambda: engine)
    assert cli.main(["summary", seeded]) == 0
    printed = capsys.readouterr().out.splitlines()

    def cli_minor(label: str) -> int:
        line = next(ln for ln in printed if ln.startswith(label))
        return int(Decimal(line.split("£")[1].replace(",", "")) * 100)

    summary = client.get("/api/summary", params={"month": seeded}).json()
    spending = client.get("/api/spending", params={"month": seeded}).json()
    assert summary["money_out"]["minor"] == cli_minor("Money out")
    assert summary["money_in"]["minor"] == cli_minor("Money in")

    # Money out is Spending + Fees; the breakdown is Spending - Refunds (ADR 0004).
    with engine.begin() as conn:
        txns = Ledger(conn, 1).transactions()

    def minor(kind: str) -> int:
        return sum(
            int(t.amount * 100)
            for t in txns
            if f"{t.posted_on:%Y-%m}" == seeded
            and t.categorisation
            and t.categorisation.kind == kind
        )

    assert spending["total"]["minor"] == -minor("Spending") - minor("Refund")
    assert summary["money_out"]["minor"] == -minor("Spending") - minor("Fee")
    # the parts the Overview prints add up to Money out exactly, as `fa summary` shows it
    parts = spending["total"], spending["refunds"], spending["fees"]
    assert sum(p["minor"] for p in parts) == cli_minor("Money out")
    assert spending["money_out"]["minor"] == cli_minor("Money out")
    assert spending["refunds"]["minor"] == minor("Refund")
    assert spending["fees"]["minor"] == -minor("Fee")


def test_a_refund_with_no_category_is_its_own_line_not_a_negative_category(
    client: TestClient, engine: Engine, write_export: Callable[[str], Path]
) -> None:
    h = history(
        [
            (1, "DEB", "TESCO STORES 1234", -4000),
            (2, "DEB", "XYZZY WIDGETS", -1500),
            (5, "DEB", "XYZZY WIDGETS REFUND", 1099),
            (6, "DEB", "MYSTERY SHOP", -700),
        ]
    )
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([write_export(render_export(h, 0, 4).text)])
        categorise.run(conn, 1)
        month = Ledger(conn, 1).latest_month()
    assert month

    body = client.get("/api/spending", params={"month": month}).json()

    by_name = {c["name"]: c["amount"]["minor"] for c in body["categories"]}
    assert all(v > 0 for v in by_name.values())
    assert body["unmatched_refunds"]["minor"] == -1099
    assert by_name["Uncategorised"] == 1500 + 700
    assert body["total"]["minor"] == 4000 + 1500 + 700 - 1099
    assert body["total"]["minor"] == (
        sum(by_name.values()) + body["unmatched_refunds"]["minor"]
    )
    assert body["money_out"]["minor"] == 4000 + 1500 + 700
    assert body["refunds"]["minor"] == 1099
