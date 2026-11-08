"""The personal map (#275): derived edges on read, `map_edge` Proposals that
do nothing until confirmed, both nodes the user's own, edges gone with either
node, and one user never reaching another's map."""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine, text

from finance_agent import api
from finance_agent.engine import map as mp
from finance_agent.engine.map import Use
from finance_agent.ledger import Ledger, UnknownTarget
from finance_agent.ledger.life_changes import Detected, LifeChanges
from finance_agent.ledger.map_edges import KINDS, MapEdges, Node, pair
from finance_agent.ledger.proposals import (
    AlreadyDecided,
    InvalidProposal,
    MapEdgeDraft,
    MemoryDraft,
    Proposals,
    Provenance,
)
from synthetic_assistant import ME, OTHER, seed

SAID = Provenance(turn_id="t1", quote="saving for a car", said_on=date(2026, 3, 1))


def _goal(engine: Engine, user: int, tags: tuple[str, ...] = ("car",)) -> int:
    with engine.begin() as conn:
        props = Proposals(conn, user)
        draft = MemoryDraft(type="goal", text="a car", tags=tags, provenance=SAID)
        return props.confirm(props.propose_memory(draft).id).result_id or 0


def _merchant(engine: Engine, user: int, name: str) -> int:
    with engine.begin() as conn:
        return next(m.id for m in Ledger(conn, user).merchants() if name in m.name)


def _edge(a: Node, b: Node) -> MapEdgeDraft:
    return MapEdgeDraft.model_validate(
        {"a": {"kind": a.kind, "id": a.id}, "b": {"kind": b.kind, "id": b.id}}
    )


@pytest.fixture
def nodes(engine: Engine, tmp_path: Path) -> dict[str, Node]:
    seed(engine, OTHER, tmp_path)
    seed(engine, ME, tmp_path)
    return {
        "goal": Node("goal", _goal(engine, ME)),
        "tesco": Node("merchant", _merchant(engine, ME, "Tesco")),
        "other_tesco": Node("merchant", _merchant(engine, OTHER, "Tesco")),
        "other_goal": Node("goal", _goal(engine, OTHER)),
    }


def _stored(engine: Engine, user: int = ME) -> list[tuple[Node, Node]]:
    with engine.begin() as conn:
        return [(e.a, e.b) for e in MapEdges(conn, user).stored()]


# --- evaluation ------------------------------------------------------------


def test_the_evaluation_reproduces_and_beats_the_baseline() -> None:
    import gate
    import map as harness

    fresh = json.loads(json.dumps(harness.evaluate()))
    assert gate.differences(fresh, json.loads(harness.RESULTS.read_text()), 0) == []
    m = fresh["models"]["map"]["metrics"]
    base = fresh["models"]["cooccurrence"]["metrics"]
    assert m["derived_precision"] == m["derived_recall"] == 1.0
    assert m["proposed_precision"] > base["proposed_precision"]
    assert m["proposed_recall"] > base["proposed_recall"]


# --- the Engine ------------------------------------------------------------


def test_derived_edges_join_merchants_goals_and_life_changes() -> None:
    cats = {1: ("Travel", "Holidays"), 2: ("Bills", "Rent or mortgage")}
    uses = [Use(10, 1), Use(11, 2, "rent"), Use(None, 1), Use(12, None)]
    got = mp.derived(uses, cats, {5: ("holiday",), 6: ("car",)}, {7: "rent"})

    def m(i: int) -> Node:
        return Node("merchant", i)

    def c(i: int) -> Node:
        return Node("category", i)

    g, lc = Node("goal", 5), Node("life_change", 7)
    assert got == {
        pair(m(10), c(1)),
        pair(m(11), c(2)),
        pair(g, c(1)),  # "holiday" names "Holidays"
        pair(lc, m(11)),
        pair(lc, c(2)),
    }
    # the car goal names no Category; a Merchant's name holds the word
    assert mp.candidates(("car",), {1: "CAR WASH", 2: "CARDIFF BAKERY"}, cats, got) == {
        1
    }
    assert mp.candidates(("holiday",), {}, cats, got) == {10}


nodes_st = st.builds(Node, st.sampled_from(KINDS), st.integers(1, 5))


@given(st.lists(st.tuples(nodes_st, nodes_st), max_size=12), st.data())
def test_the_graph_holds_every_edge_once_whichever_way_round(
    raw: list[tuple[Node, Node]], data: st.DataObject
) -> None:
    edges = {pair(a, b) for a, b in raw if a != b}
    confirmed = {e for e in edges if data.draw(st.booleans())}
    flipped = [(b, a) for a, b in confirmed]
    g = mp.graph(edges - confirmed, flipped)
    assert {(e.a, e.b) for e in g} == edges
    assert {(e.a, e.b) for e in g if e.source == "confirmed"} == confirmed
    for a, b in edges:
        assert (b, "confirmed" if (a, b) in confirmed else "derived") in mp.neighbours(
            a, g
        )


# --- map_edge Proposals ----------------------------------------------------


def test_a_map_edge_does_nothing_until_confirmed(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    with engine.begin() as conn:
        p = Proposals(conn, ME).propose_map_edge(_edge(nodes["tesco"], nodes["goal"]))
    assert p.kind == "map_edge" and _stored(engine) == []
    with engine.begin() as conn:
        done = Proposals(conn, ME).confirm(p.id)
        again = Proposals(conn, ME).confirm(p.id)  # idempotent
    assert done == again and done.result_id is not None
    assert _stored(engine) == [pair(nodes["tesco"], nodes["goal"])]
    with engine.begin() as conn:
        g = mp.read(conn, ME)
        assert mp.Edge(*pair(nodes["goal"], nodes["tesco"]), "confirmed") in g
        assert any(e.source == "derived" for e in g)  # Tesco - Groceries
        with pytest.raises(InvalidProposal, match="already"):
            Proposals(conn, ME).propose_map_edge(_edge(nodes["goal"], nodes["tesco"]))


def test_a_rejected_map_edge_leaves_no_edge(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    with engine.begin() as conn:
        props = Proposals(conn, ME)
        p = props.propose_map_edge(_edge(nodes["tesco"], nodes["goal"]))
        props.reject(p.id)
        with pytest.raises(AlreadyDecided):
            props.confirm(p.id)
    assert _stored(engine) == []


@pytest.mark.parametrize(
    "a, b",
    [
        ("tesco", "other_goal"),  # another user's goal
        ("other_tesco", "goal"),  # another user's Merchant
        ("tesco", "missing"),
        ("tesco", "not_a_goal"),  # a preference Memory is not a goal node
    ],
)
def test_proposing_needs_both_nodes_to_be_the_users(
    engine: Engine, nodes: dict[str, Node], a: str, b: str
) -> None:
    with engine.begin() as conn:
        props = Proposals(conn, ME)
        pref = MemoryDraft(type="preference", text="x", provenance=SAID)
        pref_id = props.confirm(props.propose_memory(pref).id).result_id or 0
    extra = {
        "missing": Node("category", 10**9),
        "not_a_goal": Node("goal", pref_id),
    }
    pick = {**nodes, **extra}
    with engine.begin() as conn, pytest.raises(UnknownTarget):
        Proposals(conn, ME).propose_map_edge(_edge(pick[a], pick[b]))
    with engine.begin() as conn:
        assert [p for p in Proposals(conn, ME).pending() if p.kind == "map_edge"] == []


def test_a_node_cannot_join_itself(engine: Engine, nodes: dict[str, Node]) -> None:
    with engine.begin() as conn, pytest.raises(InvalidProposal):
        Proposals(conn, ME).propose_map_edge(_edge(nodes["goal"], nodes["goal"]))


def test_confirming_after_a_node_went_leaves_it_pending(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    with engine.begin() as conn:
        p = Proposals(conn, ME).propose_map_edge(_edge(nodes["tesco"], nodes["goal"]))
        Proposals(conn, ME).delete_memory(nodes["goal"].id)
    with engine.begin() as conn, pytest.raises(UnknownTarget):
        Proposals(conn, ME).confirm(p.id)
    with engine.begin() as conn:
        assert Proposals(conn, ME).get(p.id).status == "pending"


# --- deletion --------------------------------------------------------------


def _confirmed(engine: Engine, a: Node, b: Node, user: int = ME) -> int:
    with engine.begin() as conn:
        props = Proposals(conn, user)
        return props.confirm(props.propose_map_edge(_edge(a, b)).id).id


def _proposal_rows(engine: Engine, pid: int) -> int:
    with engine.begin() as conn:
        q = text("SELECT count(*) FROM proposal WHERE id = :p")
        return conn.execute(q, {"p": pid}).scalar_one()


def test_an_edge_goes_with_its_goal_and_takes_its_proposal(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    pid = _confirmed(engine, nodes["tesco"], nodes["goal"])
    other = _confirmed(engine, nodes["other_tesco"], nodes["other_goal"], OTHER)
    with engine.begin() as conn:
        Proposals(conn, ME).delete_memory(nodes["goal"].id)
    assert _stored(engine) == [] and _proposal_rows(engine, pid) == 0
    assert len(_stored(engine, OTHER)) == 1 and _proposal_rows(engine, other) == 1


def test_an_edge_goes_with_its_life_change_and_its_merchant(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    with engine.begin() as conn:
        lc = Node(
            "life_change",
            LifeChanges(conn, ME).confirm(Detected("rent", date(2026, 1, 1), 1, 2)),
        )
    _confirmed(engine, lc, nodes["goal"])
    _confirmed(engine, nodes["tesco"], nodes["goal"])
    with engine.begin() as conn:
        assert LifeChanges(conn, ME).remove(lc.id)
    assert _stored(engine) == [pair(nodes["tesco"], nodes["goal"])]
    with engine.begin() as conn:  # Merchants are never deleted; the trigger holds
        conn.execute(text("PRAGMA foreign_keys = OFF"))
        conn.execute(
            text("DELETE FROM merchant WHERE id = :m"), {"m": nodes["tesco"].id}
        )
    assert _stored(engine) == []


def test_remove_and_forget_are_scoped_to_the_user(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    _confirmed(engine, nodes["tesco"], nodes["goal"])
    _confirmed(engine, nodes["other_tesco"], nodes["other_goal"], OTHER)
    with engine.begin() as conn:
        (mine,) = MapEdges(conn, ME).stored()
        assert MapEdges(conn, OTHER).remove(mine.id) is False
        assert MapEdges(conn, OTHER).forget() == 1
        assert MapEdges(conn, ME).stored() == [mine]
        assert MapEdges(conn, ME).remove(mine.id) is True
        assert MapEdges(conn, ME).stored() == []


def test_another_user_cannot_confirm_or_read_my_map_edge(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    with engine.begin() as conn:
        p = Proposals(conn, ME).propose_map_edge(_edge(nodes["tesco"], nodes["goal"]))
    with engine.begin() as conn, pytest.raises(UnknownTarget):
        Proposals(conn, OTHER).confirm(p.id)
    with engine.begin() as conn:
        assert all(
            n.id != nodes["tesco"].id
            for e in mp.read(conn, OTHER)
            for n in (e.a, e.b)
            if n.kind == "merchant"
        )
    assert _stored(engine) == []


def test_the_api_shows_a_pending_map_edge_card(
    engine: Engine, nodes: dict[str, Node]
) -> None:
    with engine.begin() as conn:
        Proposals(conn, ME).propose_map_edge(_edge(nodes["tesco"], nodes["goal"]))
    with TestClient(api.create_app(engine)) as c:
        (card,) = [
            i
            for i in c.get("/api/proposals").json()["items"]
            if i["kind"] == "map_edge"
        ]
    a, b = nodes["tesco"], nodes["goal"]  # as proposed
    assert card["map_edge"] == {
        "a": {"kind": a.kind, "id": a.id},
        "b": {"kind": b.kind, "id": b.id},
    }


def test_migration_0031_round_trips(tmp_path: Path) -> None:
    from test_storage import round_trip

    eng = round_trip(tmp_path, "0030", "0031", {"map_edge"}, ())
    with eng.begin() as conn:
        kinds = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'proposal'")
        ).scalar_one()
        assert "map_edge" in kinds
    eng.dispose()
