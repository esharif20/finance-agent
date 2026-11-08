"""/api/insights (#150): the ranked feed, ratings and dismissals, for
current_user only. Synthetic data only."""

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
from finance_agent.ledger.insights import Found, Insights
from test_insights import seed

Export = Callable[[str], Path]


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_list_rate_and_dismiss(
    engine: Engine,
    write_export: Export,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed(engine, write_export)
    caplog.set_level(logging.DEBUG)
    r = client.get("/api/insights")
    assert r.status_code == 200, r.text
    feed = r.json()
    assert feed and client.get("/api/insights").json() == feed  # no duplicates
    top = feed[0]
    assert top["relevance"] == {"liked": 0, "disliked": 0}
    assert top["explanation"] and top["evidence"]
    assert isinstance(top["impact_minor"], int)

    r = client.post(f"/api/insights/{top['id']}/feedback", json={"liked": True})
    assert r.json() == {"id": top["id"], "signal": "up"}
    again = client.get("/api/insights").json()
    assert again[0]["id"] == top["id"] and again[0]["signal"] == "up"
    assert again[0]["relevance"] == {"liked": 1, "disliked": 0}

    assert client.post(f"/api/insights/{top['id']}/dismiss").json()["signal"] == (
        "dismissed"
    )
    assert top["id"] not in [i["id"] for i in client.get("/api/insights").json()]
    # nothing about the Insights reaches the logs
    assert "Spotify" not in caplog.text and "SPOTIFY" not in caplog.text


def test_limit_caps_exposure(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export)
    assert len(client.get("/api/insights", params={"limit": 1}).json()) == 1
    for bad in (0, 21, -1):
        assert client.get("/api/insights", params={"limit": bad}).status_code == 422


@pytest.mark.parametrize("body", [{}, {"liked": "yes"}, {"liked": 1}, {"liked": None}])
def test_a_malformed_rating_is_refused(
    engine: Engine, write_export: Export, client: TestClient, body: object
) -> None:
    seed(engine, write_export)
    (i, *_) = client.get("/api/insights").json()
    assert (
        client.post(f"/api/insights/{i['id']}/feedback", json=body).status_code == 422
    )


def theirs(engine: Engine) -> list[int]:
    """User 2's Insights, written straight to the store."""
    with engine.begin() as conn:
        store = Insights(conn, 2)
        store.replace(
            [
                Found(
                    "recurring",
                    f"SYNTH {n}",
                    "Bills / Energy",
                    date(2026, 6, n),
                    1000 * n,
                    90,
                    (n,),
                )  # fmt: skip
                for n in (1, 2, 3)
            ]
        )
        return [i.id for i in store.current()]


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.data())
def test_feedback_on_another_users_insight_is_not_found_and_changes_nothing(
    engine: Engine, client: TestClient, data: st.DataObject
) -> None:
    ids = theirs(engine)
    target = data.draw(st.sampled_from(ids) | st.integers(1, 2**63 - 1))
    action = data.draw(st.sampled_from(("up", "down", "dismiss")))
    if action == "dismiss":
        r = client.post(f"/api/insights/{target}/dismiss")
    else:
        r = client.post(
            f"/api/insights/{target}/feedback", json={"liked": action == "up"}
        )
    assert r.status_code == 404
    assert client.get("/api/insights").json() == []  # never listed to user 1
    with engine.begin() as conn:
        assert Insights(conn, 2).signals() == []
        assert [i.signal for i in Insights(conn, 2).current()] == [None] * 3


def test_wiping_feedback_resets_relevance_and_only_this_users(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export)
    ids = theirs(engine)
    with engine.begin() as conn:
        Insights(conn, 2).give(ids[0], "up")
    a, *_ = client.get("/api/insights").json()
    client.post(f"/api/insights/{a['id']}/dismiss")
    assert client.get("/api/insights").json() == []
    assert client.delete("/api/insights/feedback").json() == {"deleted": 1}
    feed = client.get("/api/insights").json()
    assert a["id"] in {i["id"] for i in feed}  # the dismissed one is back
    assert all(i["relevance"] == {"liked": 0, "disliked": 0} for i in feed)
    with engine.begin() as conn:
        assert Insights(conn, 2).signals() == [("recurring", "Bills / Energy", True)]


def test_the_general_feedback_wipe_also_wipes_insight_ratings(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    """Verifier (#150): DATA_POLICY's one feedback wipe (`DELETE /api/feedback`,
    which the planned forget command lists) must reach Insight ratings too,
    and only this user's."""
    seed(engine, write_export)
    ids = theirs(engine)
    with engine.begin() as conn:
        Insights(conn, 2).give(ids[0], "up")
    a, *_ = client.get("/api/insights").json()
    client.post(f"/api/insights/{a['id']}/feedback", json={"liked": False})
    assert client.delete("/api/feedback").status_code == 200
    with engine.begin() as conn:
        assert Insights(conn, 1).signals() == []
        assert Insights(conn, 2).signals() == [("recurring", "Bills / Energy", True)]
