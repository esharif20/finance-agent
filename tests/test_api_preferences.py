"""/api/preferences (#262): the next pair from #255's picker, its one answer,
what was learned in words, and reset, for current_user only (user 1).
Synthetic data only."""

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, delete

import synthetic_preferences as sp
from finance_agent import api
from finance_agent.categorise import seed_taxonomy
from finance_agent.engine import insights as engine_insights
from finance_agent.engine import preferences as pref
from finance_agent.engine.detectors import Row
from finance_agent.ledger.pairwise import NotAsked, Pairwise
from finance_agent.ledger.proposals import MemoryDraft, Proposals, Provenance
from finance_agent.storage import schema

ANSWERS = 20


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


@pytest.fixture
def spending(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> tuple[sp.User, dict[str, str]]:
    """User 1 is synthetic user 0: each of its items is a Subcategory with its
    monthly spend, posted ten days ago. Returns the user and key -> item."""
    user = sp.users()[0]
    with engine.begin() as conn:
        ids = seed_taxonomy(conn, 1)
    leaves = sorted(ids.items(), key=lambda kv: kv[1])[: len(user.items)]
    on = date.today() - timedelta(days=10)
    rows = [
        Row(n, on, "synthetic", cat, -user.spend[item])
        for n, (item, (cat, _)) in enumerate(zip(user.items, leaves, strict=True), 1)
    ]
    keys = {
        f"category:{cid}": item
        for item, (_, cid) in zip(user.items, leaves, strict=True)
    }

    def rows_of(led: Any) -> list[Row]:
        return rows if led._db.user_id == 1 else []  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(engine_insights, "rows", rows_of)
    return user, keys


def test_replaying_a_synthetic_user_matches_the_offline_fit_exactly(
    engine: Engine, client: TestClient, spending: tuple[sp.User, dict[str, str]]
) -> None:
    user, item = spending
    keys = sorted(item, key=lambda k: int(k.split(":")[1]))  # the API's order
    prior = pref.prior_from_spend({k: user.spend[item[k]] for k in keys})

    offline: list[pref.Answer] = []
    for _ in range(ANSWERS):
        pair = pref.next_pair(keys, offline, prior)
        assert pair is not None
        a, b = pair
        offline.append(
            pref.Answer(a, b) if user.answer(item[a], item[b]) else pref.Answer(b, a)
        )

    asked: list[tuple[str, str]] = []
    for _ in range(ANSWERS):
        p = client.get("/api/preferences/next-pair").json()
        a, b = p["a"]["key"], p["b"]["key"]
        assert p["prompt"] == "Which would you cut first?"
        asked.append((a, b))
        cut = b if user.answer(item[a], item[b]) else a  # the loser is cut
        r = client.post(
            "/api/preferences/answer", json={"pair_id": p["pair_id"], "choice": cut}
        )
        assert r.status_code == 200, r.text

    assert [{x.winner, x.loser} for x in offline] == [set(x) for x in asked]
    with engine.begin() as conn:
        stored = [pref.Answer(w, lo) for w, lo in Pairwise(conn, 1).answers("cut")]
    assert stored == offline
    assert pref.strengths(keys, stored, prior) == pref.strengths(keys, offline, prior)

    fitted = pref.strengths(keys, offline, prior)
    got = client.get("/api/preferences").json()
    assert got["answers"] == ANSWERS
    assert [i["key"] for i in got["items"]] == sorted(keys, key=lambda k: -fitted[k])
    assert [i["rank"] for i in got["items"]] == list(range(1, len(keys) + 1))
    assert got["items"][0]["words"] == "you'd keep this longest"
    assert got["items"][-1]["words"] == "you'd cut this first"
    assert all(set(i) == {"key", "label", "rank", "words"} for i in got["items"])


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.integers(1, 2**63 - 1))
def test_answering_a_pair_never_asked_is_refused(
    client: TestClient, spending: tuple[sp.User, dict[str, str]], pair_id: int
) -> None:
    p = client.get("/api/preferences/next-pair").json()
    if pair_id == p["pair_id"]:
        pair_id += 1
    for choice in (p["a"]["key"], "category:1"):
        r = client.post(
            "/api/preferences/answer", json={"pair_id": pair_id, "choice": choice}
        )
        assert r.status_code == 404


def test_a_pair_is_answered_once_and_only_with_one_of_its_items(
    client: TestClient, spending: tuple[sp.User, dict[str, str]]
) -> None:
    p = client.get("/api/preferences/next-pair").json()
    outside = next(k for k in spending[1] if k not in (p["a"]["key"], p["b"]["key"]))
    for bad in (outside, "card:x", "category:0", "", "goal:1; DROP TABLE"):
        r = client.post(
            "/api/preferences/answer", json={"pair_id": p["pair_id"], "choice": bad}
        )
        assert r.status_code == 422
    body = {"pair_id": p["pair_id"], "choice": p["a"]["key"]}
    assert client.post("/api/preferences/answer", json=body).status_code == 200
    assert client.post("/api/preferences/answer", json=body).status_code == 404
    for malformed in (
        {},
        {"pair_id": "1", "choice": "x"},
        {"pair_id": 0, "choice": "x"},
    ):
        assert client.post("/api/preferences/answer", json=malformed).status_code == 422


def test_a_skip_records_nothing(
    engine: Engine, client: TestClient, spending: tuple[sp.User, dict[str, str]]
) -> None:
    first = client.get("/api/preferences/next-pair").json()
    second = client.get("/api/preferences/next-pair").json()  # skipped the first
    assert (first["a"], first["b"]) == (second["a"], second["b"])
    body = {"pair_id": first["pair_id"], "choice": first["a"]["key"]}
    assert client.post("/api/preferences/answer", json=body).status_code == 404
    with engine.begin() as conn:
        assert Pairwise(conn, 1).answers("cut") == []
    assert client.get("/api/preferences").json()["items"] == []


def test_another_users_pair_is_not_found_and_their_answers_unseen(
    engine: Engine, client: TestClient, spending: tuple[sp.User, dict[str, str]]
) -> None:
    with engine.begin() as conn:
        theirs = Pairwise(conn, 2)
        done = theirs.ask("matters", "card:emergency-fund", "card:student-money")
        theirs.answer(done.id, "card:emergency-fund")
        waiting = theirs.ask("cut", "card:credit-cards", "card:payday-planning")
    for pid, choice in ((waiting.id, waiting.a), (done.id, done.a)):
        r = client.post(
            "/api/preferences/answer", json={"pair_id": pid, "choice": choice}
        )
        assert r.status_code == 404
    assert (
        client.get("/api/preferences", params={"question": "matters"}).json()["answers"]
        == 0
    )
    assert client.delete("/api/preferences").json() == {"deleted": 0}
    with engine.begin() as conn:
        assert Pairwise(conn, 2).answers("matters") == [
            ("card:emergency-fund", "card:student-money")
        ]
        with pytest.raises(NotAsked):
            Pairwise(conn, 1).answer(waiting.id, waiting.a)


def test_reset_wipes_every_answer(
    engine: Engine, client: TestClient, spending: tuple[sp.User, dict[str, str]]
) -> None:
    for _ in range(3):
        p = client.get("/api/preferences/next-pair").json()
        client.post(
            "/api/preferences/answer",
            json={"pair_id": p["pair_id"], "choice": p["b"]["key"]},
        )
    assert client.get("/api/preferences").json()["answers"] == 3
    assert client.delete("/api/preferences").json() == {"deleted": 3}
    assert client.get("/api/preferences").json() == {
        "question": "cut",
        "answers": 0,
        "items": [],
    }


def test_answers_go_with_their_category(
    engine: Engine, client: TestClient, spending: tuple[sp.User, dict[str, str]]
) -> None:
    p = client.get("/api/preferences/next-pair").json()
    client.post(
        "/api/preferences/answer",
        json={"pair_id": p["pair_id"], "choice": p["a"]["key"]},
    )
    cid = int(p["a"]["key"].split(":")[1])
    with engine.begin() as conn:
        conn.execute(delete(schema.category).where(schema.category.c.id == cid))
        assert Pairwise(conn, 1).answers("cut") == []


def test_matters_is_over_goals_and_cards_and_answers_go_with_them(
    engine: Engine, client: TestClient
) -> None:
    with engine.begin() as conn:
        props = Proposals(conn, 1)
        said = Provenance(turn_id="t1", quote="synthetic", said_on=date.today())
        draft = MemoryDraft(
            type="goal", text="a synthetic goal", tags=("car",), provenance=said
        )
        props.confirm(props.propose_memory(draft).id)
        (goal,) = props.memories()
    p = client.get("/api/preferences/next-pair", params={"question": "matters"}).json()
    assert p["prompt"] == "Which matters more to you?"
    keys = {p["a"]["key"], p["b"]["key"]}
    assert keys <= {f"goal:{goal.id}"} | {
        f"card:{c}"
        for c in (
            "credit-cards",
            "emergency-fund",
            "payday-checkin",
            "payday-planning",
            "saving-for-goal",
            "small-daily-spends",
            "student-money",
            "subscription-creep",
        )
    }
    assert f"goal:{goal.id}" in keys  # ties go to the first item: the goal
    client.post(
        "/api/preferences/answer",
        json={"pair_id": p["pair_id"], "choice": f"goal:{goal.id}"},
    )
    got = client.get("/api/preferences", params={"question": "matters"}).json()
    assert got["items"][0] == {
        "key": f"goal:{goal.id}",
        "label": "a synthetic goal",
        "rank": 1,
        "words": "matters most to you",
    }
    assert client.delete(f"/api/memories/{goal.id}").status_code in (200, 204)
    with engine.begin() as conn:
        assert Pairwise(conn, 1).answers("matters") == []


def test_a_card_that_leaves_the_playbook_takes_its_answers(engine: Engine) -> None:
    with engine.begin() as conn:
        store = Pairwise(conn, 1)
        asked = store.ask("matters", "card:gone-card", "card:emergency-fund")
        store.answer(asked.id, "card:gone-card")
        assert store.drop_cards({"emergency-fund"}) == 1
        assert store.answers("matters") == []


def test_fewer_than_two_items_has_no_pair(client: TestClient) -> None:
    assert client.get("/api/preferences/next-pair").json() is None
    r = client.get("/api/preferences/next-pair", params={"question": "other"})
    assert r.status_code == 422
