"""/api/splits (#123): preview, save, read and revert a Split, for current_user
only. Amounts are integer minor units; the remainder is the server's."""

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
from test_review_queue import KNOWN, export

GROCERIES = ["Food & Drink", "Groceries"]
GIFTS = ["Shopping", "Online"]


def seed(engine: Engine, tmp_path: Path, user_id: int = 1) -> int:
    p = tmp_path / f"export{user_id}.csv"
    p.write_text(export([(KNOWN, -1200)]))
    with engine.begin() as conn:
        if user_id != 1:
            conn.execute(
                schema.user.insert().values(id=user_id, created_at=datetime.now(UTC))
            )
        led = Ledger(conn, user_id)
        led.import_files([p])
        categorise.run(conn, user_id)
        return led.transactions()[0].id


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def body(*parts: tuple[Any, list[str]]) -> dict[str, Any]:
    return {"parts": [{"amount_minor": a, "category": c} for a, c in parts]}


def test_preview_save_read_and_revert(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    tid = seed(engine, tmp_path)
    want = [
        {"amount_minor": -700, "category": GROCERIES},
        {"amount_minor": -500, "category": GIFTS},
    ]

    r = client.post(
        f"/api/splits/{tid}/preview", json=body((-700, GROCERIES), (None, GIFTS))
    )
    assert r.status_code == 200
    assert r.json()["parts"] == want
    assert client.get(f"/api/splits/{tid}").status_code == 404  # nothing saved

    r = client.put(f"/api/splits/{tid}", json=body((-700, GROCERIES), (None, GIFTS)))
    assert r.status_code == 200
    saved = r.json()
    assert saved["parts"] == want
    assert (saved["transaction_id"], saved["amount_minor"], saved["currency"]) == (
        tid,
        -1200,
        "GBP",
    )
    assert saved["needs_recheck"] is False
    assert client.get(f"/api/splits/{tid}").json() == saved

    spending = client.get("/api/spending", params={"month": "2026-03"}).json()
    assert {c["name"]: c["amount"]["minor"] for c in spending["categories"]} == {
        "Food & Drink": 700,
        "Shopping": 500,
    }
    assert spending["money_out"]["minor"] == 1200

    r = client.delete(f"/api/splits/{tid}")
    assert r.status_code == 200
    assert r.json()["id"] == saved["id"]
    assert client.get(f"/api/splits/{tid}").status_code == 404
    assert client.delete(f"/api/splits/{tid}").status_code == 404


@pytest.mark.parametrize(
    "parts",
    [
        [(0, GROCERIES), (None, GIFTS)],  # zero
        [(-1300, GROCERIES), (100, GIFTS)],  # mixed sign
        [(-100, GROCERIES)] * 10 + [(None, GIFTS)],  # eleven parts
        [(-700.0, GROCERIES), (None, GIFTS)],  # a float
        [(-700.5, GROCERIES), (None, GIFTS)],  # a fractional float
        [("-700", GROCERIES), (None, GIFTS)],  # a string
        [(-700, GROCERIES), (-400, GIFTS)],  # a wrong sum
        [(-(2**80), GROCERIES), (None, GIFTS)],  # a huge int
        [(-(2**62), GROCERIES), (-(2**62), GROCERIES), (None, GIFTS)],  # overflow
        [(-700, ["Food & Drink"]), (None, GIFTS)],  # a malformed Category
    ],
)
def test_malformed_parts_are_refused(
    engine: Engine,
    tmp_path: Path,
    client: TestClient,
    parts: list[tuple[Any, list[str]]],
) -> None:
    tid = seed(engine, tmp_path)
    for method in (client.put, client.post):
        url = f"/api/splits/{tid}" + ("/preview" if method == client.post else "")
        r = method(url, json=body(*parts))
        assert r.status_code == 422, r.text
    assert client.get(f"/api/splits/{tid}").status_code == 404


def test_another_users_transaction_is_not_found(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    theirs = seed(engine, tmp_path, user_id=2)
    seed(engine, tmp_path, user_id=1)
    split = body((-700, GROCERIES), (None, GIFTS))
    assert client.put(f"/api/splits/{theirs}", json=split).status_code == 404
    assert client.post(f"/api/splits/{theirs}/preview", json=split).status_code == 404
    assert client.get(f"/api/splits/{theirs}").status_code == 404
    assert client.delete(f"/api/splits/{theirs}").status_code == 404
    with engine.begin() as conn:
        assert Ledger(conn, 2).splits() == []


def test_an_unknown_category_is_not_found(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    tid = seed(engine, tmp_path)
    r = client.put(
        f"/api/splits/{tid}", json=body((-700, ["No", "Such"]), (None, GIFTS))
    )
    assert r.status_code == 404
