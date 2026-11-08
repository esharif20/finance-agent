"""The exposure log and off-policy evaluation (#261, #228): seeded exploration
with exact propensities, IPS and SNIPS, and the log the feed writes, for
current_user only. Synthetic data only."""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine

import gate
import ope as ope_eval
from finance_agent import api
from finance_agent.engine import insights as ins
from finance_agent.engine import ope
from finance_agent.engine.detectors import Row
from finance_agent.ledger.exposure import Exposures, Shown, week_of
from finance_agent.ledger.insights import Insights
from finance_agent.ledger.proposals import MemoryDraft, Proposals, Provenance
from test_api_guidance import (
    _rows,  # pyright: ignore[reportPrivateUsage]
    data,  # noqa: F401  # pyright: ignore[reportUnusedImport] - a fixture
)
from test_insights import seed

Export = Callable[[str], Path]
eps_st = st.fractions(0, 1, max_denominator=50)


@given(st.integers(0, 30), st.integers(0, 30), eps_st)
def test_propensities_in_each_slot_sum_to_one(n: int, k: int, eps: Fraction) -> None:
    probs = ope.propensities(n, k, eps)
    assert len(probs) == min(n, k)
    assert all(sum(d.values()) == 1 for d in probs)
    assert all(0 < p <= 1 for d in probs for p in d.values())


@given(st.integers(0, 25), st.integers(1, 25), eps_st, st.integers(0, 2**64 - 1))
def test_exploration_swaps_the_last_slot_and_never_grows_the_slate(
    n: int, k: int, eps: Fraction, s: int
) -> None:
    ranked = list(range(n))
    got = ope.slate(ranked, k, s, eps)
    probs = ope.propensities(n, k, eps)
    assert [x.slot for x in got] == list(range(min(n, k)))  # never more than k
    assert len({x.item for x in got}) == len(got)  # no repeats
    assert all(x.item in ranked for x in got)  # only candidates given
    assert [x.item for x in got[:-1]] == ranked[: len(got) - 1]
    assert all(x.propensity == probs[x.slot][x.item] for x in got)
    if k == 1 and n:
        assert got[0].item == 0  # a slate of one never explores


def test_exploration_runs_at_epsilon_and_spreads_evenly() -> None:
    shown = [ope.slate(range(10), 3, s)[-1].item for s in range(20_000)]
    assert abs(shown.count(2) / len(shown) - 0.9) < 0.01
    assert all(abs(shown.count(r) / len(shown) - 0.1 / 7) < 0.005 for r in range(3, 10))


def test_the_seed_is_stable_and_separates_its_parts() -> None:
    assert ope.seed(1, date(2026, 6, 1), "p") == ope.seed(1, date(2026, 6, 1), "p")
    assert ope.seed(1, 12) != ope.seed(11, 2)


def test_ips_and_snips_hand_cases() -> None:
    one, half = Fraction(1), Fraction(1, 2)
    logs = [ope.Logged(one, half, one), ope.Logged(Fraction(0), half, Fraction(0))]
    assert ope.ips(logs) == 1  # (1 * 1 / (1/2) + 0) / 2
    assert ope.snips(logs) == 1
    assert ope.ess(logs) == 1
    on_policy = [ope.Logged(Fraction(r), one, one) for r in (1, 0, 1, 1)]
    assert ope.ips(on_policy) == ope.snips(on_policy) == Fraction(3, 4)
    assert ope.ess(on_policy) == 4
    assert ope.ips([]) is None and ope.snips(logs[1:]) is None
    with pytest.raises(ValueError):
        ope.ips([ope.Logged(one, Fraction(0), one)])  # no support: never logged


def test_the_eval_reproduces_and_ips_needs_exploration() -> None:
    committed = json.loads(ope_eval.RESULTS.read_text())
    assert gate.differences(ope_eval.evaluate(), committed, 0.0) == []
    cand, base = (committed["models"][k]["metrics"] for k in ("candidate", "baseline"))
    bound = ope_eval.ERROR_BOUND
    assert cand["ips_error"] < bound and cand["snips_error"] < bound
    assert cand["support"] == 1.0 and cand["ess"] > 100
    assert base["ips_error"] > bound and base["support"] < 0.5


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_listing_logs_each_shown_insight_once_a_week(
    engine: Engine,
    write_export: Export,
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    seed(engine, write_export)
    caplog.set_level(logging.DEBUG)
    feed = client.get("/api/insights", params={"limit": 3}).json()
    assert client.get("/api/insights", params={"limit": 3}).json() == feed
    with engine.begin() as conn:
        log = Exposures(conn, 1).logged("insight")
        n = len(ins.ranked(conn, 1))
    assert [e.item for e in log] == [str(i["id"]) for i in feed]  # once, in order
    probs = ope.propensities(n, 3)
    assert all(e.week == week_of(date.today()) for e in log)
    assert all(e.policy == ope.POLICY and e.outcome is None for e in log)
    assert [e.slate_size for e in log] == [len(feed)] * len(log)
    assert all(e.propensity in probs[e.slot].values() for e in log)
    assert "Spotify" not in caplog.text and "SPOTIFY" not in caplog.text

    # only an explicit rating or dismissal is an outcome
    top = feed[0]["id"]
    client.post(f"/api/insights/{top}/feedback", json={"liked": True})
    with engine.begin() as conn:
        got = {e.item: e.outcome for e in Exposures(conn, 1).logged()}
    assert got[str(top)] == "accepted"
    assert all(o is None for i, o in got.items() if i != str(top))
    client.post(f"/api/insights/{top}/dismiss")
    with engine.begin() as conn:
        got = {e.item: e.outcome for e in Exposures(conn, 1).logged()}
    assert got[str(top)] == "dismissed"


def test_exploration_never_shows_what_a_constraint_rules_out(
    engine: Engine, write_export: Export, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(engine, write_export)
    with engine.begin() as conn:
        ins.refresh(conn, 1)
        props = Proposals(conn, 1)
        said = Provenance(turn_id="t1", quote="synthetic", said_on=date(2026, 6, 5))
        draft = MemoryDraft(
            type="constraint", text="synthetic", tags=("streaming",), provenance=said
        )
        props.confirm(props.propose_memory(draft).id)
        allowed = {r.insight.id for r in ins.ranked(conn, 1)}
        assert len(Insights(conn, 1).current()) > len(allowed)
        for s in range(300):  # every week's draw, explore or not

            def fixed(*_: object, s: int = s) -> int:
                return s

            monkeypatch.setattr(ope, "seed", fixed)
            for k in (1, 2, 3):
                shown = ins.shown(conn, 1, k, date(2026, 6, 5))
                assert {r.insight.id for r in shown} <= allowed
                assert "Spotify" not in {r.insight.key for r in shown}


def test_the_briefing_logs_its_insights(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export)
    b = client.get("/api/briefing", params={"on": "2026-06-10"}).json()
    with engine.begin() as conn:
        log = Exposures(conn, 1).logged("insight")
    assert [e.item for e in log] == [str(i["id"]) for i in b["insights"]]
    assert {e.week for e in log} == {week_of(date(2026, 6, 10))}


def test_shown_cards_are_logged_with_propensity_one(
    data: dict[int, list[Row]],  # noqa: F811
    engine: Engine,
    client: TestClient,
) -> None:
    data[1] = _rows()
    cards = client.get("/api/guidance").json()
    assert cards
    with engine.begin() as conn:
        log = Exposures(conn, 1).logged("card")
    assert [e.item for e in log] == [c["id"] for c in cards]
    assert all(e.propensity == 1 and e.slate_size == len(cards) for e in log)


def test_another_users_log_is_neither_read_nor_changed(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export, user_id=2)
    with engine.begin() as conn:
        ins.refresh(conn, 2)
        theirs = ins.shown(conn, 2, 3, date.today())
        assert Exposures(conn, 1).logged() == []
        mine = Exposures(conn, 1)
        assert not mine.record("insight", str(theirs[0].insight.id), "accepted")
    assert client.delete("/api/insights/exposures").json() == {"deleted": 0}
    with engine.begin() as conn:
        log = Exposures(conn, 2).logged()
    assert len(log) == len(theirs) and all(e.outcome is None for e in log)


def test_the_log_is_wiped_by_either_path(
    engine: Engine, write_export: Export, client: TestClient
) -> None:
    seed(engine, write_export)
    n = len(client.get("/api/insights").json())
    assert client.delete("/api/insights/exposures").json() == {"deleted": n}
    client.get("/api/insights")
    assert client.delete("/api/feedback").json()["deleted"] >= n
    with engine.begin() as conn:
        assert Exposures(conn, 1).logged() == []


def test_rows_past_retention_go_on_the_next_write(engine: Engine) -> None:
    today = date(2026, 6, 10)
    old = today - timedelta(days=400 + 7)
    one = Shown("card", "emergency-fund", 0, 1, Fraction(1))
    with engine.begin() as conn:
        log = Exposures(conn, 1)
        log.log(old, "p", [one])
        log.log(today, "p", [one])
        assert [e.week for e in log.logged()] == [week_of(today)]


@pytest.mark.parametrize(
    "bad",
    [
        Shown("model_route", "4b", 0, 1, Fraction(1), {"query": "what did a car cost"}),
        Shown("model_route", "4b", 0, 1, Fraction(1), {"Query Text": 1}),
        Shown("insight", "spent at Synth Cafe", 0, 1, Fraction(1)),
        Shown("insight", "1", 0, 1, Fraction(0)),
        Shown("insight", "1", 0, 1, Fraction(3, 2)),
        Shown("insight", "1", 1, 1, Fraction(1)),
    ],
)
def test_text_and_impossible_propensities_are_refused(
    engine: Engine, bad: Shown
) -> None:
    with engine.begin() as conn, pytest.raises(ValueError):
        Exposures(conn, 1).log(date(2026, 6, 10), "p", [bad])


def test_a_model_route_logs_features_and_cluster_only(engine: Engine) -> None:
    route = Shown(
        "model_route", "4b", 0, 1, Fraction(17, 20), {"cluster": 7, "f_len": 12}
    )
    with engine.begin() as conn:
        Exposures(conn, 1).log(date(2026, 6, 10), "router-abc", [route])
        (e,) = Exposures(conn, 1).logged("model_route")
    assert e.context == {"cluster": 7, "f_len": 12}
    assert e.propensity == Fraction(17, 20)
