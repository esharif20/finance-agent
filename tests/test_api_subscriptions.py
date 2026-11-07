"""/api/subscriptions (#103): the registry's endpoints, scoped by current_user."""

from collections.abc import Iterator
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ledger import Ledger
from finance_agent.storage import Scoped, schema


def merchant(engine: Engine, name: str, user_id: int = 1) -> int:
    with engine.begin() as conn:
        return Scoped(conn, user_id).insert(schema.merchant, name=name)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_add_regroup_and_remove(engine: Engine, client: TestClient) -> None:
    m = merchant(engine, "SYNTH STREAM")

    r = client.post(
        "/api/subscriptions",
        json={"merchant_id": m, "kind": "subscription", "group": "Streaming"},
    )
    assert r.status_code == 201
    (e,) = client.get("/api/subscriptions").json()
    assert {k: e[k] for k in ("merchant_id", "merchant", "group", "reason")} == {
        "merchant_id": m,
        "merchant": "SYNTH STREAM",
        "group": "Streaming",
        "reason": "user_added",
    }

    r = client.patch(
        f"/api/subscriptions/{m}", json={"kind": "subscription", "group": "Software"}
    )
    assert r.status_code == 200 and r.json()["group"] == "Software"

    r = client.delete(f"/api/subscriptions/{m}")
    assert r.status_code == 200 and r.json()["status"] == "not_subscription"


def test_confirm_and_dismiss_a_detection(engine: Engine, client: TestClient) -> None:
    a, b = merchant(engine, "SYNTH AI"), merchant(engine, "SYNTH CLUB")
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        for m, g in ((a, "AI tools"), (b, "Memberships")):
            led.record_detection(
                m, "subscription", g, "pattern", "suggested", date(2026, 1, 1), 3
            )

    r = client.post(f"/api/subscriptions/{a}/confirm", json={})
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["reason"], r.json()["source"]) == (
        "active",
        "user_confirmed",
        "user",
    )
    assert r.json()["evidence"]["charge_count"] == 3
    r = client.post(f"/api/subscriptions/{b}/dismiss")
    assert r.status_code == 200 and r.json()["status"] == "not_subscription"


def test_bad_input_is_refused(engine: Engine, client: TestClient) -> None:
    m = merchant(engine, "SYNTH ENERGY")
    bad = {"merchant_id": m, "kind": "bill", "group": "Streaming"}
    assert client.post("/api/subscriptions", json=bad).status_code == 422
    assert client.patch(f"/api/subscriptions/{m}", json=bad).status_code == 404
    assert client.post(f"/api/subscriptions/{m}/confirm", json={}).status_code == 404
    unknown = {"merchant_id": 999, "kind": "bill", "group": "Energy"}
    assert client.post("/api/subscriptions", json=unknown).status_code == 404


def test_another_users_entries_are_unreachable(
    engine: Engine, client: TestClient
) -> None:
    """current_user() is 1; user 2's Merchant ids must behave as unknown."""
    theirs = merchant(engine, "SYNTH STREAM", user_id=2)
    with engine.begin() as conn:
        Ledger(conn, 2).add_subscription(theirs, "subscription", "Streaming")

    assert client.get("/api/subscriptions").json() == []
    body = {"kind": "subscription", "group": "Software"}
    for r in (
        client.post("/api/subscriptions", json={"merchant_id": theirs, **body}),
        client.patch(f"/api/subscriptions/{theirs}", json=body),
        client.post(f"/api/subscriptions/{theirs}/confirm", json=body),
        client.post(f"/api/subscriptions/{theirs}/dismiss"),
        client.delete(f"/api/subscriptions/{theirs}"),
    ):
        assert r.status_code == 404
    with engine.begin() as conn:
        (e,) = Ledger(conn, 2).subscriptions()
    assert (e.group, e.status, e.reason) == ("Streaming", "active", "user_added")
