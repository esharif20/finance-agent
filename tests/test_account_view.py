"""The per-Account view and the combined "All accounts" view (#59).

Combined money in and out leave out Transfers between the user's own Accounts
(Kind Transfer, GLOSSARY) and report them on their own line."""

import tempfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api, categorise, identity
from finance_agent.cli import main
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import MAX_ROWS, Txn, generate_histories, render_export


def own(t: Txn) -> bool:  # the generator's own-account Transfer rows
    return "SYNTH SAVER" in t.description or "SYNTH CURRENT" in t.description


def pence(d: Decimal) -> int:
    return int(d.scaleb(2))


@settings(max_examples=12, deadline=None)
@given(st.integers(0, 2**32 - 1), st.integers(10, 70))
def test_combined_equals_the_accounts_net_of_transfers_between_them(
    seed: int, days: int
) -> None:
    current, saver = generate_histories(seed, days)
    current = replace(current, txns=current.txns[:MAX_ROWS])
    with tempfile.TemporaryDirectory() as d:
        paths: list[Path] = []
        for i, h in enumerate((current, saver)):
            p = Path(d) / f"export{i}.csv"
            p.write_text(render_export(h, 0, len(h.txns)).text)
            paths.append(p)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            ledger.import_files(paths)
            linked = {
                t.id for t in ledger.transactions() if t.transfer_with is not None
            }
            stored = ledger.transactions()
            ids = [a.id for a in ledger.accounts()]
            for m in ledger.months():
                whole = ledger.summarise_month(m)
                parts = [ledger.summarise_month(m, i) for i in ids]
                # independent truth: every stored row of the month, by sign
                rows = [t for t in stored if t.posted_on.strftime("%Y-%m") == m]
                gross_in = sum(pence(t.amount) for t in rows if t.amount > 0)
                gross_out = sum(-pence(t.amount) for t in rows if t.amount < 0)
                moved_in = sum(
                    pence(t.amount) for t in rows if t.amount > 0 and t.id in linked
                )
                moved_out = sum(
                    -pence(t.amount) for t in rows if t.amount < 0 and t.id in linked
                )
                assert pence(whole.money_in) == gross_in - moved_in
                assert pence(whole.money_out) == gross_out - moved_out
                assert pence(whole.transfers_in) == moved_in
                assert pence(whole.transfers_out) == moved_out
                # each Account's own money in plus its Transfers in is all it got;
                # adding the Accounts and taking off the moves gives the combined
                assert sum(pence(p.money_in + p.transfers_in) for p in parts) - pence(
                    whole.transfers_in
                ) == pence(whole.money_in)
                assert sum(pence(p.money_out + p.transfers_out) for p in parts) - pence(
                    whole.transfers_out
                ) == pence(whole.money_out)
                assert sum(p.transfers_in for p in parts) == whole.transfers_in
        engine.dispose()


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


@pytest.fixture
def two_accounts(
    engine: Engine, write_export: Callable[[str], Path]
) -> tuple[str, list[int]]:
    current, saver = generate_histories(seed=8, days=60)
    files = [write_export(render_export(h, 0, 120).text) for h in (current, saver)]
    with engine.begin() as conn:
        ledger = Ledger(conn, identity.current_user())
        ledger.import_files(files)
        categorise.run(conn, identity.current_user())
        month = ledger.latest_month()
        assert month
        return month, [a.id for a in ledger.accounts()]


def test_combined_summary_breaks_down_by_account_and_adds_up(
    client: TestClient, two_accounts: tuple[str, list[int]]
) -> None:
    month, ids = two_accounts
    whole = client.get("/api/summary", params={"month": month}).json()
    assert whole["transfers_out"]["minor"] > 0  # seed 8 moves money that month
    shares = {s["account_id"]: s for s in whole["accounts"]}
    assert set(shares) == set(ids)
    for key in ("money_in", "money_out", "transfers_in", "transfers_out"):
        assert sum(s[key]["minor"] for s in shares.values()) == whole[key]["minor"]
    for i in ids:
        one = client.get("/api/summary", params={"month": month, "account_id": i})
        body = one.json()
        assert body["accounts"] == []  # the breakdown is for the combined view
        for key in ("money_in", "money_out", "transfers_in", "transfers_out"):
            assert body[key] == shares[i][key]


def test_combined_view_agrees_with_fa_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    current, saver = generate_histories(seed=8, days=60)
    for i, h in enumerate((current, saver)):
        (tmp_path / f"e{i}.csv").write_text(render_export(h, 0, 120).text)
    assert main(["import", str(tmp_path / "e0.csv"), str(tmp_path / "e1.csv")]) == 0
    engine = open_db(tmp_path / "ledger.db")
    with TestClient(api.create_app(engine)) as c:
        body = c.get("/api/summary").json()
    engine.dispose()
    capsys.readouterr()
    assert main(["summary", body["month"]]) == 0
    out = capsys.readouterr().out

    def gbp(m: dict[str, Any]) -> str:
        v = Decimal(m["minor"]).scaleb(-2)
        return f"{'-' if v < 0 else ''}£{abs(v):,.2f}"

    assert f"Money in   {gbp(body['money_in'])}" in out
    assert f"Money out  {gbp(body['money_out'])}" in out
    assert f"Net        {gbp(body['net'])}" in out
    assert f"Transfers  {gbp(body['transfers_out'])} between your accounts" in out


def test_spending_lists_top_merchants_and_category_filters_by_account(
    client: TestClient, engine: Engine, two_accounts: tuple[str, list[int]]
) -> None:
    month, ids = two_accounts
    for i in [None, *ids]:
        p: dict[str, Any] = {"month": month}
        if i is not None:
            p["account_id"] = i
        s = client.get("/api/spending", params=p).json()
        ms = s["merchants"]
        assert len(ms) <= 5
        amounts = [m["amount"]["minor"] for m in ms]
        assert amounts == sorted(amounts, reverse=True)
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        whole = led.spending_by_category(month)
        top = whole[0].name
        per = [led.spending_by_category(month, i) for i in ids]
    parts: list[int] = []
    for i in ids:
        r = client.get(
            f"/api/categories/{top}", params={"month": month, "account_id": i}
        )
        parts.append(r.json()["total"]["minor"] if r.status_code == 200 else 0)
        assert r.status_code in (200, 404)
    assert sum(parts) == pence(whole[0].amount)
    assert parts == [
        next((pence(s.amount) for s in xs if s.name == top), 0) for xs in per
    ]
    assert (
        client.get(
            f"/api/categories/{top}", params={"month": month, "account_id": 9999}
        ).status_code
        == 404
    )


def test_accounts_say_when_they_were_last_imported(
    client: TestClient, two_accounts: tuple[str, list[int]]
) -> None:
    body = client.get("/api/accounts").json()
    assert all(a["last_import"] and a["source"] == "csv" for a in body)
