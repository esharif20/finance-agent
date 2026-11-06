"""GET /api/month-series: money in/out per month for the Overview bars."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise, identity
from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


@pytest.fixture
def seeded(engine: Engine, write_export: Callable[[str], Path]) -> None:
    h, _ = generate_histories(seed=8, days=130)
    with engine.begin() as conn:
        Ledger(conn, identity.current_user()).import_files(
            [write_export(render_export(h, 0, 150).text)]
        )
        categorise.run(conn, identity.current_user())


def test_each_bar_equals_the_ledger_summary_oldest_first(
    client: TestClient, engine: Engine, seeded: None
) -> None:
    body = client.get("/api/month-series", params={"limit": 2}).json()

    with engine.begin() as conn:
        led = Ledger(conn, 1)
        newest_two = led.months()[:2]
        want = [led.summarise_month(m) for m in reversed(newest_two)]
    assert len(body) == 2
    assert [b["month"] for b in body] == [s.month for s in want]
    assert [b["money_in"]["minor"] for b in body] == [
        int(s.money_in * 100) for s in want
    ]
    assert [b["money_out"]["minor"] for b in body] == [
        int(s.money_out * 100) for s in want
    ]


def test_limit_is_bounded_and_empty_ledger_is_an_empty_list(
    client: TestClient,
) -> None:
    assert client.get("/api/month-series").json() == []
    assert client.get("/api/month-series", params={"limit": 0}).status_code == 422
    assert client.get("/api/month-series", params={"limit": 61}).status_code == 422
