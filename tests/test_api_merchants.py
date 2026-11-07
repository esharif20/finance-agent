"""/api/merchants (#125): the "Always ask" flag and its suggestion, for
current_user only. Synthetic data only."""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.ledger import Ledger
from finance_agent.storage import schema
from test_review_queue import export

CAFE = "SYNTH CAFE"


def seed(engine: Engine, tmp_path: Path, user_id: int = 1) -> int:
    """Two payments at an Uncategorised Merchant; returns the Merchant id."""
    p = tmp_path / f"export{user_id}.csv"
    p.write_text(export([(CAFE, -350), (CAFE, -900)]))
    with engine.begin() as conn:
        if user_id != 1:
            conn.execute(
                schema.user.insert().values(id=user_id, created_at=datetime.now(UTC))
            )
        led = Ledger(conn, user_id)
        led.import_files([p])
        categorise.run(conn, user_id)
        return next(m.id for m in led.merchants() if m.name == CAFE)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def merchant(client: TestClient, mid: int) -> dict[str, Any]:
    return next(
        m for m in client.get("/api/merchants").json()["items"] if m["id"] == mid
    )


def to_review(client: TestClient) -> str | None:
    got = client.get("/api/spending").json()
    return next(
        (c["amount"] for c in got["categories"] if c["name"] == "To review"), None
    )


def test_suggest_then_flag_switches_off_the_merchant_wide_correction(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    mid = seed(engine, tmp_path)
    assert merchant(client, mid)["always_ask"] is False
    tid = client.get("/api/transactions").json()["items"][0]["id"]
    for target in ({"transaction_id": tid}, {"merchant_id": mid}):
        cat = (
            ["Food & Drink", "Coffee"]
            if "merchant_id" in target
            else ["Shopping", "Online"]
        )
        r = client.post("/api/corrections", json={**target, "category": cat})
        assert r.status_code == 201, r.text
    m = merchant(client, mid)
    assert (m["suggest_always_ask"], m["always_ask"]) == (True, False)
    assert to_review(client) is None

    r = client.put(
        f"/api/merchants/{mid}/always-ask", json={"on": True, "source": "suggestion"}
    )
    assert r.status_code == 200, r.text
    got = r.json()
    assert len(got["switched_off_corrections"]) == 1
    assert "Merchant-wide Correction is off" in got["message"]
    assert got["merchant"]["always_ask_source"] == "suggestion"
    assert got["merchant"]["suggest_always_ask"] is False
    assert to_review(client) is not None  # the uncorrected payment waits
    queue = client.get("/api/review-queue").json()["items"]
    assert [i["type"] for i in queue].count("always_ask") == 1
    r = client.post(
        "/api/corrections",
        json={"merchant_id": mid, "category": ["Food & Drink", "Coffee"]},
    )
    assert r.status_code == 409

    r = client.put(f"/api/merchants/{mid}/always-ask", json={"on": False})
    assert r.status_code == 200
    assert r.json()["switched_off_corrections"] == []
    assert "Nothing" in r.json()["message"]
    assert to_review(client) is None


@pytest.mark.parametrize(
    "body", [{"on": True, "source": "categoriser"}, {"on": "yes"}, {}]
)
def test_bad_bodies_are_refused(
    engine: Engine, tmp_path: Path, client: TestClient, body: dict[str, Any]
) -> None:
    mid = seed(engine, tmp_path)
    assert client.put(f"/api/merchants/{mid}/always-ask", json=body).status_code == 422
    assert merchant(client, mid)["always_ask"] is False


def test_another_users_merchant_is_not_found(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    theirs = seed(engine, tmp_path, user_id=2)
    seed(engine, tmp_path, user_id=1)
    for mid in (theirs, 0, 2**63):
        r = client.put(f"/api/merchants/{mid}/always-ask", json={"on": True})
        assert r.status_code in (404, 422)
    r = client.put(f"/api/merchants/{theirs}/always-ask", json={"on": True})
    assert r.status_code == 404
    assert theirs not in {m["id"] for m in client.get("/api/merchants").json()["items"]}
    with engine.begin() as conn:
        assert all(m.always_ask is None for m in Ledger(conn, 2).merchants())


def test_merchant_names_and_amounts_never_reach_the_logs(
    engine: Engine,
    tmp_path: Path,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mid = seed(engine, tmp_path)
    with caplog.at_level(logging.DEBUG):
        client.put(f"/api/merchants/{mid}/always-ask", json={"on": True})
        client.get("/api/merchants")
    assert "SYNTH" not in caplog.text and "3.50" not in caplog.text
