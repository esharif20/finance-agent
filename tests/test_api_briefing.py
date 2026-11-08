"""/api/briefing (#152): the weekly briefing, regret answers and their wipe,
for current_user only. Synthetic data only."""

import logging
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ledger import Ledger
from finance_agent.ledger.regret import Regret
from test_insights import seed

Export = Callable[[str], Path]
ON = {"on": "2026-06-17"}  # a Wednesday; its week starts 2026-06-15
ON_WEEK = date(2026, 6, 15)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_the_briefing_matches_the_insight_feed_and_takes_answers(
    engine: Engine,
    write_export: Export,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed(engine, write_export)
    caplog.set_level(logging.DEBUG)
    r = client.get("/api/briefing", params=ON)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["week"] == "2026-06-15"
    assert b["insufficient"] == ["forecast"] and b["forecast"] is None
    # parity: the same Insights, figures and all, as the feed's own endpoint
    feed = client.get("/api/insights", params={"limit": 3}).json()
    assert b["insights"] == feed and feed
    (item,) = b["regret"]
    assert item["amount"] == {"minor": -320, "currency": "GBP"}
    assert item["worth_it"] is None

    tid = item["transaction_id"]
    r = client.post(f"/api/briefing/regret/{tid}", params=ON, json={"worth_it": False})
    assert r.status_code == 200, r.text
    again = client.get("/api/briefing", params=ON).json()
    assert [(i["transaction_id"], i["worth_it"]) for i in again["regret"]] == [
        (tid, False)
    ]  # the week's list is unchanged, now answered
    # the next week, the answered item is not asked again
    assert (
        client.get("/api/briefing", params={"on": "2026-06-22"}).json()["regret"] == []
    )
    # an answer is feedback, never a Memory
    assert client.get("/api/memories").json() == {"items": []}
    assert "SYNTH CAFE" not in caplog.text and "320" not in caplog.text


def test_only_this_weeks_items_can_be_answered(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        spotify = next(
            t.id for t in Ledger(conn, 1).transactions() if "SPOTIFY" in t.description
        )
    r = client.post(
        f"/api/briefing/regret/{spotify}", params=ON, json={"worth_it": True}
    )
    assert r.status_code == 404  # a subscription is never a regret item


@pytest.mark.parametrize(
    "body", [{}, {"worth_it": "yes"}, {"worth_it": 1}, {"worth_it": None}]
)
def test_a_malformed_answer_is_refused(
    engine: Engine, write_export: Export, client: TestClient, body: object
) -> None:
    seed(engine, write_export)
    (item,) = client.get("/api/briefing", params=ON).json()["regret"]
    r = client.post(
        f"/api/briefing/regret/{item['transaction_id']}", params=ON, json=body
    )
    assert r.status_code == 422


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.data())
def test_another_users_transactions_cannot_be_answered_or_briefed(
    engine: Engine, write_export: Export, client: TestClient, data: st.DataObject
) -> None:
    with engine.begin() as conn:
        fresh = not Ledger(conn, 2).transactions()
    if fresh:
        seed(engine, write_export, user_id=2)
    with engine.begin() as conn:
        theirs = [t.id for t in Ledger(conn, 2).transactions()]
    target = data.draw(st.sampled_from(theirs) | st.integers(1, 2**63 - 1))
    r = client.post(
        f"/api/briefing/regret/{target}", params=ON, json={"worth_it": True}
    )
    assert r.status_code == 404
    b = client.get("/api/briefing", params=ON).json()
    assert b["insights"] == [] and b["regret"] == [] and b["renewals"] == []
    assert b["insufficient"] == ["insights", "forecast"]
    with engine.begin() as conn:
        assert Regret(conn, 2).answers() == {}


def test_wiping_regret_answers_only_wipes_this_users(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export)
    seed(engine, write_export, user_id=2)
    with engine.begin() as conn:
        theirs = next(
            t.id for t in Ledger(conn, 2).transactions() if "CAFE" in t.description
        )
        Regret(conn, 2).answer(theirs, ON_WEEK, True)
    (item,) = client.get("/api/briefing", params=ON).json()["regret"]
    tid = item["transaction_id"]
    client.post(f"/api/briefing/regret/{tid}", params=ON, json={"worth_it": True})
    assert client.delete("/api/briefing/regret").json() == {"deleted": 1}
    (item,) = client.get("/api/briefing", params=ON).json()["regret"]
    assert item["worth_it"] is None
    with engine.begin() as conn:
        assert list(Regret(conn, 2).answers()) == [theirs]


@pytest.mark.parametrize("bad", ["0", "-1", str(2**63)])
def test_an_out_of_range_id_is_refused(client: TestClient, bad: str) -> None:
    r = client.post(f"/api/briefing/regret/{bad}", json={"worth_it": True})
    assert r.status_code == 422
