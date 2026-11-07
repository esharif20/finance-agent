"""/api/notes (#124): purpose notes on a Transaction or a Split part, for
current_user only. Refused text is never echoed or logged."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ledger import Ledger
from test_api_splits import GIFTS, GROCERIES, body, seed


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_set_edit_read_search_and_purge(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    tid = seed(engine, tmp_path)
    assert client.get(f"/api/notes/{tid}").status_code == 404
    r = client.put(f"/api/notes/{tid}", json={"text": "school shoes"})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/notes/{tid}", json={"text": "school shoes and socks"})
    got = client.get(f"/api/notes/{tid}").json()
    assert got == r.json()
    assert (got["transaction_id"], got["part"], got["text"]) == (
        tid,
        None,
        "school shoes and socks",
    )
    assert [v["text"] for v in got["history"]] == [
        "school shoes",
        "school shoes and socks",
    ]
    found = client.get("/api/transactions", params={"q": "SOCKS"}).json()
    assert [t["id"] for t in found["items"]] == [tid]

    r = client.delete(f"/api/notes/{tid}")
    assert r.status_code == 200 and r.json()["purged"] == 2
    assert client.get(f"/api/notes/{tid}").status_code == 404
    assert client.delete(f"/api/notes/{tid}").status_code == 404
    assert client.get("/api/transactions", params={"q": "socks"}).json()["total"] == 0


def test_a_split_part_note(engine: Engine, tmp_path: Path, client: TestClient) -> None:
    tid = seed(engine, tmp_path)
    assert (
        client.put(f"/api/notes/{tid}?part=1", json={"text": "gift"}).status_code == 404
    )
    client.put(f"/api/splits/{tid}", json=body((-700, GROCERIES), (None, GIFTS)))
    r = client.put(f"/api/notes/{tid}?part=1", json={"text": "gift"})
    assert r.status_code == 200 and r.json()["part"] == 1
    assert client.get(f"/api/notes/{tid}").status_code == 404  # the whole has none
    assert client.get(f"/api/notes/{tid}?part=1").json()["text"] == "gift"
    for bad in ("-1", "10", "x"):
        assert client.get(f"/api/notes/{tid}?part={bad}").status_code == 422


@pytest.mark.parametrize(
    "text",
    ["SYNTHSECRET" + "x" * 130, "SYNTHSECRET\nline", "SYNTHSECRET\x1b[31m", ""],
)
def test_bad_text_is_refused_never_echoed_and_never_logged(
    text: str,
    engine: Engine,
    tmp_path: Path,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tid = seed(engine, tmp_path)
    with caplog.at_level(logging.DEBUG):
        r = client.put(f"/api/notes/{tid}", json={"text": text})
        ok = client.put(f"/api/notes/{tid}", json={"text": "SYNTHSECRET fine"})
    assert r.status_code == 422
    assert ok.status_code == 200
    assert "SYNTHSECRET" not in r.text
    assert "SYNTHSECRET" not in caplog.text


@pytest.mark.parametrize("text", [None, 5, ["a"], {"a": 1}])
def test_a_non_string_is_refused(
    text: object, engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    tid = seed(engine, tmp_path)
    assert client.put(f"/api/notes/{tid}", json={"text": text}).status_code == 422


def test_another_users_transaction_is_not_found(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    theirs = seed(engine, tmp_path, user_id=2)
    seed(engine, tmp_path, user_id=1)
    with engine.begin() as conn:
        Ledger(conn, 2).set_note(theirs, "their note")
    assert client.put(f"/api/notes/{theirs}", json={"text": "x"}).status_code == 404
    assert client.get(f"/api/notes/{theirs}").status_code == 404
    assert client.delete(f"/api/notes/{theirs}").status_code == 404
    assert client.get("/api/transactions", params={"q": "their"}).json()["total"] == 0
    with engine.begin() as conn:
        assert [n.text for n in Ledger(conn, 2).note_history(theirs)] == ["their note"]


def test_the_transactions_list_carries_no_note_text(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    """Notes are read only from /api/notes; the rows assistant tools will read
    (#136) never carry them, so nothing sends a note by default (AI_DESIGN)."""
    tid = seed(engine, tmp_path)
    client.put(f"/api/notes/{tid}", json={"text": "SYNTHNOTE"})
    r = client.get("/api/transactions", params={"q": "synthnote"})
    assert r.json()["total"] == 1 and "SYNTHNOTE" not in r.text
