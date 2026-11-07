"""/api/subscriptions/costs and /cancel (#105), scoped by current_user.
Synthetic data only."""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.ledger import Ledger

Export = Callable[[str], Path]


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def seed(engine: Engine, export: Export, user_id: int = 1) -> int:
    """Netflix charged monthly up to a week ago; returns its Merchant id."""
    last = date.today() - timedelta(days=7)
    rows = "".join(
        f"{last - timedelta(days=30 * i):%d/%m/%Y},NETFLIX.COM,-10.99\n"
        for i in range(3)
    )
    with engine.begin() as conn:
        Ledger(conn, user_id).import_files([export(rows)])
        categorise.run(conn, user_id)
        (e,) = Ledger(conn, user_id).subscriptions()
        return e.merchant_id


def test_costs_list_status_monthly_cost_and_totals(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    m = seed(engine, write_export)
    body = client.get("/api/subscriptions/costs").json()
    (item,) = body["items"]
    assert (item["merchant_id"], item["status"], item["interval"]) == (
        m,
        "active",
        "monthly",
    )
    assert item["monthly"] == {"minor": 1099, "currency": "GBP"}
    assert item["counted"] is True and item["cost_note"] is None
    assert body["totals"] == [
        {"kind": "subscription", "monthly": {"minor": 1099, "currency": "GBP"}}
    ]
    assert body["groups"] == [
        {
            "kind": "subscription",
            "group": "Streaming",
            "monthly": {"minor": 1099, "currency": "GBP"},
        }
    ]
    assert body["rate_needed"] == 0


def test_cancel_takes_it_out_of_the_totals(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    m = seed(engine, write_export)
    r = client.post(f"/api/subscriptions/{m}/cancel")
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["source"]) == ("user_cancelled", "user")
    body = client.get("/api/subscriptions/costs").json()
    assert body["items"][0]["status"] == "cancelled"
    assert body["totals"] == [] and body["groups"] == []
    # confirming lists it as active again
    client.post(f"/api/subscriptions/{m}/confirm", json={})
    assert client.get("/api/subscriptions/costs").json()["items"][0]["counted"]


def test_another_users_subscriptions_are_unreachable(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    """current_user() is 1; user 2's entries and Merchant ids are unknown."""
    theirs = seed(engine, write_export, user_id=2)
    body = client.get("/api/subscriptions/costs").json()
    assert body == {"items": [], "totals": [], "groups": [], "rate_needed": 0}
    assert client.post(f"/api/subscriptions/{theirs}/cancel").status_code == 404
    with engine.begin() as conn:
        (e,) = Ledger(conn, 2).subscriptions()
    assert (e.status, e.source) == ("active", "detection")


@pytest.mark.parametrize("bad", [0, -1, 2**63, 2**80])
def test_an_out_of_range_merchant_id_is_refused(client: TestClient, bad: int) -> None:
    assert client.post(f"/api/subscriptions/{bad}/cancel").status_code == 422


def test_cancelling_a_negative_or_unknown_entry_is_404(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    m = seed(engine, write_export)
    client.delete(f"/api/subscriptions/{m}")  # "not a subscription"
    assert client.post(f"/api/subscriptions/{m}/cancel").status_code == 404
    assert client.post("/api/subscriptions/999/cancel").status_code == 404
