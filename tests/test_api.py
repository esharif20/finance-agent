"""The API is a thin adapter over the Ledger interface (FastAPI test client)."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise, identity, storage
from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


@pytest.fixture
def seeded(engine: Engine, write_export: Callable[[str], Path]) -> str:
    """Imports a synthetic history with a 12.40 gap; returns its latest month."""
    h, _ = generate_histories(seed=8, days=60)
    with engine.begin() as conn:
        ledger = Ledger(conn, identity.current_user())
        ledger.import_files(
            [write_export(render_export(h, 0, 120, gap_at=60, gap_minor=1240).text)]
        )
        return ledger.transactions()[-1].posted_on.strftime("%Y-%m")


def test_server_binds_to_localhost_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(app: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    def no_db(**_: object) -> None:  # binding needs no database
        return None

    monkeypatch.setattr(api, "open_db", no_db)

    api.serve(port=9999)

    assert calls[0]["host"] == "127.0.0.1"
    assert identity.HOST == "127.0.0.1"


def test_serving_never_creates_the_ledger(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = tmp_path / "nowhere" / "ledger.db"
    monkeypatch.setenv("FA_DB_PATH", str(db))

    def served(*_: object, **__: object) -> None:
        pytest.fail("served")

    monkeypatch.setattr(uvicorn, "run", served)

    with pytest.raises(storage.MissingDatabase):
        api.serve(port=9999)

    assert not db.parent.exists()


def test_current_user_is_the_single_local_user() -> None:
    assert identity.current_user() == 1


def test_health_identifies_the_app(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"app": "finance-agent"}


def test_summary_returns_what_the_ledger_returns_in_minor_units(
    client: TestClient, engine: Engine, seeded: str
) -> None:
    with engine.begin() as conn:
        expected = Ledger(conn, 1).summarise_month(seeded)

    body = client.get("/api/summary", params={"month": seeded}).json()

    assert body["month"] == seeded
    assert body["money_in"] == {
        "minor": int(expected.money_in * 100),
        "currency": "GBP",
    }
    assert body["money_out"]["minor"] == int(expected.money_out * 100)
    assert body["net"]["minor"] == int(expected.net * 100)
    assert isinstance(body["net"]["minor"], int)


def test_summary_defaults_to_the_latest_month_with_data(
    client: TestClient, seeded: str
) -> None:
    assert client.get("/api/summary").json()["month"] == seeded


def test_a_bad_month_is_a_422_and_an_empty_ledger_has_no_summary(
    client: TestClient,
) -> None:
    assert client.get("/api/summary", params={"month": "nope"}).status_code == 422
    assert client.get("/api/summary").status_code == 404


def test_accounts_carry_balances_and_gaps(
    client: TestClient, engine: Engine, seeded: str
) -> None:
    with engine.begin() as conn:
        [want] = Ledger(conn, 1).accounts()

    [acct] = client.get("/api/accounts").json()

    assert acct["masked_number"] == want.masked_number
    assert acct["bank_balance"]["minor"] == int(want.bank_balance * 100)
    assert acct["ledger_balance"]["minor"] == int(want.ledger_balance * 100)
    assert acct["gaps"][0]["amount"]["minor"] == 1240
    assert acct["bank_balance"]["minor"] - acct["ledger_balance"]["minor"] == 1240


def test_the_built_web_app_is_served_when_present(
    engine: Engine, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<html>hello</html>")
    with TestClient(api.create_app(engine, web_dir=tmp_path)) as c:
        assert "hello" in c.get("/").text
        assert c.get("/api/health").status_code == 200


@pytest.fixture
def categorised(engine: Engine, seeded: str) -> str:
    with engine.begin() as conn:
        categorise.run(conn, identity.current_user())
    return seeded


def _all(client: TestClient) -> list[dict[str, Any]]:
    res = client.get("/api/transactions", params={"limit": 500}).json()
    return res["items"]  # type: ignore[no-any-return]


def test_transactions_list_newest_first_and_filter(
    client: TestClient, categorised: str
) -> None:
    body = client.get("/api/transactions", params={"limit": 500}).json()
    dates = [t["posted_on"] for t in body["items"]]
    assert dates == sorted(dates, reverse=True) and body["total"] == len(dates)
    assert all(isinstance(t["amount"]["minor"], int) for t in body["items"])

    month = client.get("/api/transactions", params={"month": categorised}).json()
    assert month["items"]
    assert all(t["posted_on"].startswith(categorised) for t in month["items"])

    kind = client.get("/api/transactions", params={"kind": "Income"}).json()
    assert kind["items"] and {t["kind"] for t in kind["items"]} == {"Income"}

    top = next(t["category"][0] for t in body["items"] if t["category"])
    cat = client.get("/api/transactions", params={"category": top}).json()
    assert cat["items"] and {t["category"][0] for t in cat["items"]} == {top}

    acct = body["items"][0]["account_id"]
    acc = client.get("/api/transactions", params={"account_id": acct}).json()
    assert {t["account_id"] for t in acc["items"]} == {acct}

    page = client.get("/api/transactions", params={"limit": 3, "offset": 1}).json()
    assert [t["id"] for t in page["items"]] == [t["id"] for t in body["items"][1:4]]
    assert page["total"] == body["total"]


def test_transaction_search_matches_description_and_merchant(
    client: TestClient, categorised: str
) -> None:
    target = next(t for t in _all(client) if t["merchant"])
    word = target["merchant"].split()[0].lower()
    hits = client.get("/api/transactions", params={"q": word, "limit": 500}).json()
    assert target["id"] in {t["id"] for t in hits["items"]}
    assert all(
        word in (t["description"] + (t["merchant"] or "")).lower()
        for t in hits["items"]
    )
    assert client.get("/api/transactions", params={"q": "zzzqqq"}).json()["total"] == 0


def test_correction_round_trip_changes_category_and_totals(
    client: TestClient, engine: Engine, categorised: str
) -> None:
    spend = next(
        t
        for t in _all(client)
        if t["kind"] == "Spending" and t["posted_on"].startswith(categorised)
    )
    new = ["Entertainment", "Games"]
    if spend["category"] == new:
        new = ["Housing", "Rent"]
    with engine.begin() as conn:
        before = Ledger(conn, 1).spending_by_category(categorised)

    res = client.post(
        "/api/corrections", json={"transaction_id": spend["id"], "category": new}
    )

    assert res.status_code == 201 and res.json()["ledger_version"] >= 1
    row = next(t for t in _all(client) if t["id"] == spend["id"])
    assert row["category"] == new and row["source"] == "correction"
    with engine.begin() as conn:
        after = Ledger(conn, 1).spending_by_category(categorised)
    assert after != before
    assert sum((s.amount for s in after), Decimal(0)) == sum(
        (s.amount for s in before), Decimal(0)
    )


def test_correction_for_a_merchant_covers_all_its_transactions(
    client: TestClient, categorised: str
) -> None:
    spending = [t for t in _all(client) if t["kind"] == "Spending" and t["merchant_id"]]
    mid = next(
        t["merchant_id"]
        for t in spending
        if sum(1 for u in spending if u["merchant_id"] == t["merchant_id"]) > 1
    )
    new = ["Entertainment", "Games"]

    res = client.post("/api/corrections", json={"merchant_id": mid, "category": new})

    assert res.status_code == 201
    assert {
        tuple(t["category"])
        for t in _all(client)
        if t["merchant_id"] == mid and t["kind"] == "Spending"
    } == {tuple(new)}


@pytest.mark.parametrize(
    "body,status",
    [
        ({"category": ["Entertainment", "Games"]}, 422),  # no target
        ({"transaction_id": 1, "merchant_id": 1, "category": ["Housing", "Rent"]}, 422),
        ({"transaction_id": 1, "category": ["Housing"]}, 422),
        ({"transaction_id": 10**9, "category": ["Housing", "Rent"]}, 404),
        ({"transaction_id": 1, "category": ["Nope", "Nope"]}, 404),
    ],
)
def test_bad_corrections_are_refused(
    client: TestClient, categorised: str, body: dict[str, object], status: int
) -> None:
    assert client.post("/api/corrections", json=body).status_code == status


def test_a_correction_on_a_categoryless_kind_is_a_422(
    client: TestClient, categorised: str
) -> None:
    income = next(t for t in _all(client) if t["kind"] == "Income")
    res = client.post(
        "/api/corrections",
        json={"transaction_id": income["id"], "category": ["Housing", "Rent"]},
    )
    assert res.status_code == 422
    assert "Income" in res.json()["detail"]


def test_deleting_a_correction_reverts_it(client: TestClient, categorised: str) -> None:
    spend = next(t for t in _all(client) if t["kind"] == "Spending")
    new = (
        ["Housing", "Rent"]
        if spend["category"] != ["Housing", "Rent"]
        else ["Travel", "Hotels"]
    )
    made = client.post(
        "/api/corrections", json={"transaction_id": spend["id"], "category": new}
    ).json()

    res = client.delete(f"/api/corrections/{made['id']}")

    assert res.status_code == 200
    assert res.json()["ledger_version"] == made["ledger_version"] + 1
    row = next(t for t in _all(client) if t["id"] == spend["id"])
    assert row["category"] == spend["category"] and row["source"] == spend["source"]
    assert client.delete(f"/api/corrections/{made['id']}").status_code == 404


def test_deleting_an_unknown_or_another_users_correction_is_a_404(
    client: TestClient,
    engine: Engine,
    categorised: str,
    write_export: Callable[[str], Path],
) -> None:
    h, _ = generate_histories(seed=3, days=30)
    with engine.begin() as conn:
        theirs = Ledger(conn, 2)
        theirs.import_files([write_export(render_export(h, 0, 60).text)])
        categorise.run(conn, 2)
        spend = next(
            t
            for t in theirs.transactions()
            if t.categorisation and t.categorisation.kind == "Spending"
        )
        k = theirs.correct_transaction(spend.id, ("Housing", "Rent"))

    assert client.delete("/api/corrections/999999").status_code == 404
    assert client.delete("/api/corrections/abc").status_code == 422
    assert client.delete(f"/api/corrections/{k.id}").status_code == 404
    with engine.begin() as conn:
        assert Ledger(conn, 2).corrections()[0].reverted_at is None


def test_categories_list_the_taxonomy(client: TestClient) -> None:
    body = client.get("/api/categories").json()
    assert {c["name"]: tuple(c["subs"]) for c in body} == categorise.TAXONOMY
