"""POST /api/review-queue/answers and batch undo (#215 D)."""

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from finance_agent.storage import Scoped, open_db, schema
from test_api_review_queue import seed
from test_review_queue import export

COFFEE = ["Food & Drink", "Coffee"]
URL = "/api/review-queue/answers"


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def batch(items: list[dict[str, Any]], cat: list[str] = COFFEE) -> dict[str, Any]:
    return {
        "answers": [
            {"id": i["id"], "version": i["version"], "category": cat} for i in items
        ]
    }


def queue(client: TestClient) -> dict[str, Any]:
    return client.get("/api/review-queue").json()


def test_batch_applies_every_answer_and_reports_spend(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    items = queue(client)["items"]
    assert items[0]["spend_minor"] == 700 and items[0]["currency"] == "GBP"

    r = client.post(URL, json=batch(items))

    assert r.status_code == 200
    assert r.json()["items"] == [] and r.json()["applied"] == 2
    with engine.begin() as conn:
        assert len(Ledger(conn, 1).corrections()) == 2


def test_batch_corrections_match_the_single_answer_path(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    cafe, garage = queue(client)["items"]
    client.post(
        f"/api/review-queue/{cafe['id']}/answer",
        json={"version": cafe["version"], "category": COFFEE},
    )
    client.post(URL, json=batch([garage]))
    with engine.begin() as conn:
        single, bulk = Ledger(conn, 1).corrections()
        categorise.run(conn, 1)
        sources = {
            t.categorisation.merchant_id: t.categorisation.source
            for t in Ledger(conn, 1).transactions()
            if t.categorisation
        }
    assert single.category == bulk.category == tuple(COFFEE)
    assert set(sources.values()) == {"correction"}  # the same source for both
    assert single.merchant_id is not None and bulk.merchant_id is not None
    assert single.transaction_id is None and bulk.transaction_id is None


def test_batch_is_all_or_nothing_and_names_the_stale_items(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    cafe, garage = queue(client)["items"]
    before = queue(client)
    with engine.begin() as conn:
        v0 = Scoped(conn, 1).ledger_version()

    r = client.post(URL, json=batch([cafe, {**garage, "version": "stale"}]))

    assert r.status_code == 409
    assert r.json()["detail"]["stale_ids"] == [garage["id"]]
    assert queue(client) == before
    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == []
        assert Scoped(conn, 1).ledger_version() == v0


def test_a_failure_mid_batch_rolls_back_what_was_applied(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    cafe, garage = queue(client)["items"]
    body = batch([cafe])
    body["answers"].append(
        {"id": garage["id"], "version": garage["version"], "category": ["No", "Such"]}
    )

    assert client.post(URL, json=body).status_code == 404

    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == []
    assert queue(client)["remaining"] == 2


def test_replaying_a_batch_applies_nothing_twice(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    body = batch(queue(client)["items"])
    assert client.post(URL, json=body).status_code == 200
    with engine.begin() as conn:
        first = Ledger(conn, 1).corrections()

    r = client.post(URL, json=body)

    assert r.status_code == 409 and len(r.json()["detail"]["stale_ids"]) == 2
    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == first


def test_batch_is_capped_non_empty_and_has_no_repeats(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    one = batch(queue(client)["items"][:1])["answers"][0]

    def many(n: int) -> list[dict[str, Any]]:
        return [{**one, "id": f"category:{k + 100}"} for k in range(n)]

    for answers, status in (
        ([], 422),
        ([one, one], 422),
        (many(501), 422),
        (many(500), 409),  # at the cap: valid, but none of them exist
    ):
        r = client.post(URL, json={"answers": answers})
        assert r.status_code == status, len(answers)
    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == []


def test_an_unknown_item_refuses_the_whole_batch(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    cafe, _ = queue(client)["items"]
    bad = batch([cafe])
    bad["answers"].append({"id": "subscription:9", "version": "x", "category": COFFEE})

    assert client.post(URL, json=bad).status_code == 409

    with engine.begin() as conn:
        assert Ledger(conn, 1).corrections() == []


def test_batch_undo_restores_every_row_exactly(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    before = queue(client)
    with engine.begin() as conn:
        rows_before = [t.categorisation for t in Ledger(conn, 1).transactions()]
    n = client.post(URL, json=batch(before["items"])).json()["applied"]

    assert client.post(f"/api/review-queue/undo?count={n}").status_code == 200

    assert queue(client)["items"] == before["items"]
    with engine.begin() as conn:
        assert [t.categorisation for t in Ledger(conn, 1).transactions()] == rows_before
    assert client.post("/api/review-queue/undo?count=2").status_code == 404
    assert client.post("/api/review-queue/undo?count=0").status_code == 422


def test_undoing_more_than_there_is_undoes_nothing(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path)
    client.post(URL, json=batch(queue(client)["items"][:1]))

    assert client.post("/api/review-queue/undo?count=2").status_code == 404

    with engine.begin() as conn:
        assert len(Ledger(conn, 1).corrections()) == 1  # still applied


def test_batch_cannot_reach_another_users_items(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, tmp_path, user_id=2)
    with engine.begin() as conn:
        v0 = Scoped(conn, 2).ledger_version()
        theirs = [
            {"id": i.id, "version": i.version} for i in ReviewQueue(conn, 2).items()
        ]

    assert client.post(URL, json=batch(theirs)).status_code == 409

    with engine.begin() as conn:
        assert Ledger(conn, 2).corrections() == []
        assert Scoped(conn, 2).ledger_version() == v0


@given(st.sets(st.integers(0, 5), min_size=1, max_size=6))
@settings(max_examples=12, deadline=None)
def test_a_batch_never_touches_the_other_users_rows(picks: set[int]) -> None:
    """Property: whatever subset one user answers, the other user's
    Corrections, queue and ledger version are untouched."""
    rows = [(f"SYNTH SHOP {n}", -100 * (n + 1)) for n in range(6)]
    with tempfile.TemporaryDirectory() as d:
        eng = open_db(Path(d) / "db" / "ledger.db")
        for uid in (1, 2):
            p = Path(d) / f"e{uid}.csv"
            p.write_text(export(rows))
            with eng.begin() as conn:
                if uid != 1:
                    now = datetime.now(UTC)
                    conn.execute(schema.user.insert().values(id=uid, created_at=now))
                Ledger(conn, uid).import_files([p])
                categorise.run(conn, uid)
        with eng.begin() as conn:
            theirs_before = ReviewQueue(conn, 2).items()
            v0 = Scoped(conn, 2).ledger_version()
            mine = sorted(ReviewQueue(conn, 1).items(), key=lambda i: i.title)
        idx = {n % len(mine) for n in picks}  # some names may be rule-matched
        chosen = [{"id": mine[n].id, "version": mine[n].version} for n in idx]
        with TestClient(api.create_app(eng)) as c:
            assert c.post(URL, json=batch(chosen)).status_code == 200
        with eng.begin() as conn:
            assert Ledger(conn, 2).corrections() == []
            assert ReviewQueue(conn, 2).items() == theirs_before
            assert Scoped(conn, 2).ledger_version() == v0
            assert len(Ledger(conn, 1).corrections()) == len(idx)
        eng.dispose()
