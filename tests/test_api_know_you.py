"""/api/know-you (#282): the bandit's shadow standing, confirmed life changes and
map edges, each reset wiping only its own store for current_user. Synthetic."""

from collections.abc import Iterator
from datetime import date
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from finance_agent import api
from finance_agent.ledger.exposure import Exposures, Shown
from finance_agent.ledger.life_changes import Detected, LifeChanges
from finance_agent.ledger.map_edges import MapEdges, Node
from finance_agent.ledger.pairwise import Pairwise
from synthetic_assistant import ME, OTHER, seed
from test_engine_map import (
    _confirmed,  # pyright: ignore[reportPrivateUsage]
    _goal,  # pyright: ignore[reportPrivateUsage]
    _merchant,  # pyright: ignore[reportPrivateUsage]
)

DAY = date(2026, 3, 2)
HALF = Fraction(1, 2)


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def _exposure(
    engine: Engine, user: int, kind: Any, item: str, outcome: Any, ts_q: int
) -> None:
    with engine.begin() as conn:
        log = Exposures(conn, user)
        log.log(DAY, "p", [Shown(kind, item, 0, 1, HALF, {"ts_q": ts_q})])
        log.record(kind, item, outcome)


def _counts(engine: Engine, user: int) -> dict[str, int]:
    with engine.begin() as conn:
        return {
            "exposures": len(Exposures(conn, user).logged()),
            "life": len(LifeChanges(conn, user).confirmed()),
            "edges": len(MapEdges(conn, user).stored()),
        }


@pytest.fixture
def stores(engine: Engine, tmp_path: Path) -> None:
    """Both users hold a row in every store this page resets."""
    seed(engine, OTHER, tmp_path)
    seed(engine, ME, tmp_path)
    for user in (ME, OTHER):
        goal = Node("goal", _goal(engine, user))
        tesco = Node("merchant", _merchant(engine, user, "Tesco"))
        _confirmed(engine, goal, tesco, user)
        _exposure(engine, user, "insight", "7", "accepted", 100)
        _exposure(engine, user, "model_route", "fast", "accepted", 100)
        with engine.begin() as conn:
            LifeChanges(conn, user).confirm(
                Detected("rent", date(2026, 2, 1), 100, 200)
            )


@pytest.mark.usefixtures("stores")
def test_summary_reports_standing_changes_and_edges(
    client: TestClient, engine: Engine
) -> None:
    _exposure(engine, ME, "insight", "8", "rejected", 100)
    body = client.get("/api/know-you").json()
    assert body["bandit"] == {"rated": 2, "live_pct": 50, "shadow_pct": 50}
    assert body["life_changes"] == [{"series": "rent", "month": "2026-02"}]
    assert body["map_edges"] == 1


def test_shadow_standing_weights_by_the_bandits_chance(
    client: TestClient, engine: Engine
) -> None:
    _exposure(engine, ME, "insight", "1", "accepted", 100)  # bandit would always show
    _exposure(engine, ME, "insight", "2", "rejected", 0)  # bandit would never show
    got = client.get("/api/know-you").json()["bandit"]
    assert got == {"rated": 2, "live_pct": 50, "shadow_pct": 100}


def test_no_outcomes_means_no_standing(client: TestClient) -> None:
    body = client.get("/api/know-you").json()
    assert body["bandit"] == {"rated": 0, "live_pct": None, "shadow_pct": None}
    assert body["life_changes"] == [] and body["map_edges"] == 0


@pytest.mark.usefixtures("stores")
@pytest.mark.parametrize(
    ("path", "mine"),
    [
        ("bandit", {"exposures": 1}),  # the model route's log stays
        ("life-changes", {"life": 0}),
        ("map", {"edges": 0}),
    ],
)
def test_each_reset_wipes_only_its_store_for_me(
    client: TestClient, engine: Engine, path: str, mine: dict[str, int]
) -> None:
    before, theirs = _counts(engine, ME), _counts(engine, OTHER)
    assert client.delete(f"/api/know-you/{path}").json() == {"deleted": 1}
    assert _counts(engine, ME) == before | mine
    assert _counts(engine, OTHER) == theirs


@pytest.mark.usefixtures("stores")
def test_resetting_pairwise_answers_touches_no_other_store(
    client: TestClient, engine: Engine
) -> None:
    before, theirs = _counts(engine, ME), _counts(engine, OTHER)
    with engine.begin() as conn:
        for user in (ME, OTHER):
            Pairwise(conn, user).ask("matters", "card:x", "card:y")
    assert client.delete("/api/preferences").json() == {"deleted": 1}
    with engine.begin() as conn:
        assert Pairwise(conn, OTHER).forget() == 1  # theirs survived
    assert _counts(engine, ME) == before and _counts(engine, OTHER) == theirs


def _rows(engine: Engine) -> dict[str, list[Any]]:
    q = {
        "proposal": "SELECT id, user_id, kind FROM proposal WHERE kind != 'map_edge'",
        "memory": "SELECT id, user_id, text FROM memory",
        "transaction": 'SELECT id, user_id, amount_minor FROM "transaction"',
        "correction": "SELECT * FROM correction",
    }
    with engine.begin() as conn:
        return {k: list(conn.execute(text(v)).all()) for k, v in q.items()}


@pytest.mark.usefixtures("stores")
@pytest.mark.parametrize("path", ["bandit", "life-changes", "map"])
def test_a_reset_ignores_smuggled_ids_and_spares_the_ledger(
    client: TestClient, engine: Engine, path: str
) -> None:
    """#282 verifier: ids or a user in the query or body never widen a reset,
    and no reset reaches Transactions, Corrections, Memories or a Proposal of
    another kind (the map's own Proposals go with its edges). Synthetic."""
    before, theirs, rows = _counts(engine, ME), _counts(engine, OTHER), _rows(engine)
    got = client.request(
        "DELETE",
        f"/api/know-you/{path}?user_id={OTHER}&id=1&kind=memory",
        json={"user_id": OTHER, "ids": [1, 2, 3], "all": True},
    )
    assert got.json() == {"deleted": 1}
    assert _counts(engine, OTHER) == theirs
    assert _rows(engine) == rows
    mine = _counts(engine, ME)
    assert sum(before[k] - mine[k] for k in before) == 1
