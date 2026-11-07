"""GET /api/transactions/{id} (#184): one Transaction, for current_user only."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from test_api_splits import seed


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_one_transaction_has_the_list_row_shape(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    tid = seed(engine, tmp_path)
    r = client.get(f"/api/transactions/{tid}")
    assert r.status_code == 200, r.text
    row = next(
        x for x in client.get("/api/transactions").json()["items"] if x["id"] == tid
    )
    assert r.json() == row


def test_unknown_and_another_users_transaction_are_404(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    theirs = seed(engine, tmp_path, user_id=2)
    seed(engine, tmp_path, user_id=1)
    assert client.get(f"/api/transactions/{theirs}").status_code == 404
    assert client.get("/api/transactions/999999").status_code == 404
    assert client.get("/api/transactions/0").status_code == 422
