"""/api/review-queue (#110): list, answer, skip and undo, scoped by current_user."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from finance_agent.storage import Scoped, schema
from test_review_queue import export


def seed(engine: Engine, tmp_path: Path, user_id: int = 1) -> None:
    rows = [("SYNTH CAFE", -350)] * 2 + [("SYNTH GARAGE", -4000)]
    p = tmp_path / f"export{user_id}.csv"
    p.write_text(export(rows))
    with engine.begin() as conn:
        if user_id != 1:
            conn.execute(
                schema.user.insert().values(id=user_id, created_at=datetime.now(UTC))
            )
        Ledger(conn, user_id).import_files([p])
        categorise.run(conn, user_id)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_list_answer_skip_and_undo(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    q = client.get("/api/review-queue").json()
    assert q["remaining"] == 2
    cafe, garage = q["items"]
    assert (cafe["type"], cafe["title"], cafe["weight"], cafe["choices"]) == (
        "category",
        "SYNTH CAFE",
        2,
        [],
    )

    r = client.post(f"/api/review-queue/{garage['id']}/skip", json=garage)
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [cafe["id"], garage["id"]]

    body = {"version": cafe["version"], "category": ["Food & Drink", "Coffee"]}
    r = client.post(f"/api/review-queue/{cafe['id']}/answer", json=body)
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [garage["id"]]
    assert r.json()["ledger_version"] > q["ledger_version"]

    r = client.post("/api/review-queue/undo")
    assert r.status_code == 200 and r.json()["remaining"] == 2
    client.post("/api/review-queue/undo")
    assert client.get("/api/review-queue").json()["items"] == q["items"]
    assert client.post("/api/review-queue/undo").status_code == 404


def test_bad_answers_are_refused(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    item = client.get("/api/review-queue").json()["items"][0]
    url = f"/api/review-queue/{item['id']}/answer"
    cat = ["Travel", "Hotels"]
    for body, status in (
        ({"version": "stale", "category": cat}, 409),
        ({"version": item["version"], "choice": "yes"}, 422),
        ({"version": item["version"], "category": cat, "transaction_id": 2**80}, 422),
        ({"version": item["version"], "category": ["No", "Such"]}, 404),
        ({"version": item["version"], "category": ["Travel"]}, 422),
        ({"category": cat}, 422),
    ):
        assert client.post(url, json=body).status_code == status, body
    r = client.post("/api/review-queue/category:1'%20OR%201=1/answer", json=item)
    assert r.status_code == 404
    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == []


def test_another_users_items_are_unreachable(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    """current_user() is 1; user 2's items and decisions behave as unknown."""
    seed(engine, tmp_path, user_id=2)
    with engine.begin() as conn:
        theirs = ReviewQueue(conn, 2).items()
        ReviewQueue(conn, 2).skip(theirs[0].id, theirs[0].version)
    assert client.get("/api/review-queue").json()["items"] == []
    cat = ["Travel", "Hotels"]
    for i in theirs:
        body = {"version": i.version, "category": cat}
        assert (
            client.post(f"/api/review-queue/{i.id}/answer", json=body).status_code
            == 404
        )
        assert (
            client.post(f"/api/review-queue/{i.id}/skip", json=body).status_code == 404
        )
    assert client.post("/api/review-queue/undo").status_code == 404  # their skip
    with engine.begin() as conn:
        assert Ledger(conn, 2).corrections() == []
        after = ReviewQueue(conn, 2).items()
        assert [i.skipped for i in after] == [False, True]
        assert Scoped(conn, 1).ledger_version() == 0


def test_undo_of_an_answer_reverted_elsewhere_returns_a_message(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    cafe = client.get("/api/review-queue").json()["items"][0]
    body = {"version": cafe["version"], "category": ["Food & Drink", "Coffee"]}
    client.post(f"/api/review-queue/{cafe['id']}/answer", json=body)
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        (k,) = led.corrections()
        led.revert_correction(k.id)

    r = client.post("/api/review-queue/undo")

    assert r.status_code == 200 and "already" in r.json()["message"]
    assert client.get("/api/review-queue").json()["message"] is None


def test_a_no_merchant_answer_cannot_reach_another_users_row(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    """Adversarial (#158): each user has a no-Merchant row; user 1 aims an
    answer at user 2's row by its item id and by transaction_id."""
    for uid in (1, 2):
        p = tmp_path / f"nomerchant{uid}.csv"
        p.write_text(export([("0000 1234", -500)]))
        with engine.begin() as conn:
            if uid != 1:
                conn.execute(
                    schema.user.insert().values(id=uid, created_at=datetime.now(UTC))
                )
            Ledger(conn, uid).import_files([p])
            categorise.run(conn, uid)
    with engine.begin() as conn:
        (theirs,) = ReviewQueue(conn, 2).items()
    (mine,) = client.get("/api/review-queue").json()["items"]
    assert mine["id"] != theirs.id and mine["weight"] == 1
    cat = ["Travel", "Hotels"]

    r = client.post(
        f"/api/review-queue/{theirs.id}/answer",
        json={"version": theirs.version, "category": cat},
    )
    assert r.status_code == 404
    r = client.post(
        f"/api/review-queue/{mine['id']}/answer",
        json={
            "version": mine["version"],
            "category": cat,
            "transaction_id": theirs.transaction_ids[0],
        },
    )
    assert r.status_code == 422
    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == Ledger(conn, 2).corrections() == []
