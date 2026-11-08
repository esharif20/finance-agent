"""The contextual Thompson-sampling bandit in shadow (#272): rewards only from
explicit outcomes, the weekly cap, determinism, the shadow hook on the
Insight feed, and the evaluation. Synthetic data only."""

import json
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path
from typing import get_args

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine

import bandit as bandit_eval
import gate
from finance_agent.engine import bandit, ope
from finance_agent.engine import insights as ins
from finance_agent.ledger.exposure import Exposures, Outcome, week_of
from test_insights import seed

Export = Callable[[str], Path]
CTX = bandit.Context(0, 1)
TYPES = ("recurring", "card")
CATS = ("a", "b")

arms_st = st.lists(
    st.builds(
        bandit.Arm,
        st.integers(0, 10_000).map(str),
        st.sampled_from(TYPES),
        st.sampled_from(CATS),
    ),
    max_size=12,
    unique_by=lambda a: a.item,
)
seen_st = st.lists(
    st.builds(
        bandit.Observed,
        st.sampled_from(TYPES),
        st.sampled_from(CATS),
        st.builds(bandit.Context, st.integers(0, 2), st.integers(0, 2)),
        st.sampled_from(sorted(bandit.REWARD)),
    ),
    max_size=40,
)


def test_reward_comes_only_from_explicit_outcomes() -> None:
    assert set(bandit.REWARD) == set(get_args(Outcome))
    assert {o for o, r in bandit.REWARD.items() if r} == {
        "accepted",
        "acted",
        "regret_worth_it",
    }
    for engagement in ("opened", "clicked", "viewed", "time_on_screen", "", "shown"):
        with pytest.raises(ValueError):
            bandit.reward(engagement)
        with pytest.raises(ValueError):
            bandit.learn([bandit.Observed("recurring", "a", CTX, engagement)])


@given(arms_st, seen_st, st.integers(-2, 8), st.integers(-2, 8), st.integers())
def test_never_exceeds_the_cap_and_a_seed_repeats(
    arms: list[bandit.Arm],
    seen: list[bandit.Observed],
    k: int,
    used: int,
    s: int,
) -> None:
    model = bandit.learn(seen)
    d = bandit.choose(arms, model, CTX, k, s, used)
    assert len(d.slate) <= max(0, min(bandit.CAP - used, k))
    assert len(d.slate) == max(0, min(bandit.CAP - used, k, len(arms)))
    assert d == bandit.choose(arms, model, CTX, k, s, used)
    assert [p.slot for p in d.slate] == list(range(len(d.slate)))
    assert len({p.arm.item for p in d.slate}) == len(d.slate)
    assert all(0 < p.propensity <= 1 for p in d.slate)
    assert all(p.propensity == d.membership[p.arm.item] for p in d.slate)
    assert sum(d.membership.values()) == len(d.slate)


def test_a_dominant_arm_still_cannot_break_the_cap() -> None:
    seen = [bandit.Observed("recurring", "a", CTX, "accepted")] * 500
    arms = [bandit.Arm(str(j), "recurring", "a") for j in range(20)]
    for k in (3, 10, 1_000):
        assert len(bandit.choose(arms, bandit.learn(seen), CTX, k, 1).slate) == 3
    assert bandit.choose(arms, bandit.learn(seen), CTX, 10, 1, used=3).slate == []


def test_it_learns_a_context_dependent_preference() -> None:
    late, early = bandit.Context(2, 1), bandit.Context(0, 1)
    seen = [bandit.Observed("card", "a", late, "acted")] * 30 + [
        bandit.Observed("card", "a", early, "dismissed")
    ] * 30
    model = bandit.learn(seen)
    arms = [bandit.Arm("x", "card", "a"), bandit.Arm("y", "recurring", "b")]
    hi = bandit.choose(arms, model, late, 1, 7).membership["x"]
    lo = bandit.choose(arms, model, early, 1, 7).membership["x"]
    assert hi > Fraction(1, 2) > lo and hi - lo > Fraction(1, 4)


def test_context_buckets() -> None:
    assert [bandit.time_of_month(date(2026, 6, d)) for d in (1, 10, 11, 21, 30)] == [
        0,
        0,
        1,
        2,
        2,
    ]
    day = date(2026, 6, 30)
    usual = [(day - timedelta(40 + 28 * j), -3_000) for j in range(3)]
    assert bandit.spend_level(usual + [(day, -3_000)], day) == 1
    assert bandit.spend_level(usual + [(day, -4_000)], day) == 2
    assert bandit.spend_level(usual + [(day, -2_000)], day) == 0
    assert bandit.spend_level([(day, -2_000)], day) == 1  # no usual yet
    assert bandit.code("Food & Drink / Coffee") == bandit.code("Food & Drink / Coffee")
    assert len(bandit.code("Food & Drink / Coffee")) == 12


def test_the_shadow_logs_what_it_would_show_and_the_live_feed_still_serves(
    engine: Engine, write_export: Export, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(engine, write_export)
    day = date(2026, 6, 5)
    learned: list[list[bandit.Observed]] = []
    real = bandit.learn

    def spy(seen: Iterable[bandit.Observed]) -> bandit.Model:
        learned.append(list(seen))
        return real(learned[-1])

    monkeypatch.setattr(bandit, "learn", spy)
    with engine.begin() as conn:
        ins.refresh(conn, 1)
        feed = ins.ranked(conn, 1)
        live = ope.slate(feed, 3, ope.seed(1, week_of(day), ope.POLICY))
        shown = ins.shown(conn, 1, 3, day)
        assert shown == [s.item for s in live]  # the live ranker serves
        log = Exposures(conn, 1).logged("insight")
    assert learned == [[]]  # nothing explicit yet: listing is not an outcome
    ids = {str(r.insight.id) for r in feed}
    for e in log:
        assert e.policy == ope.POLICY
        would = [e.context[f"ts{j}"] for j in range(3) if f"ts{j}" in e.context]
        assert 0 < len(would) <= bandit.CAP and set(would) <= ids
        assert 0 <= int(e.context["ts_q"]) <= bandit.DRAWS + 1
        assert {"cat", "tom", "spend"} <= e.context.keys()
        assert all(not isinstance(v, str) or " " not in v for v in e.context.values())

    with engine.begin() as conn:
        assert Exposures(conn, 1).record("insight", log[0].item, "accepted")
        ins.shown(conn, 1, 3, day + timedelta(7))
    c = log[0].context
    assert learned[-1] == [
        bandit.Observed(
            str(c["type"]),
            str(c["cat"]),
            bandit.Context(int(c["tom"]), int(c["spend"])),
            "accepted",
        )
    ]


def test_the_eval_reproduces_and_the_bandit_beats_its_baselines() -> None:
    committed = json.loads(bandit_eval.RESULTS.read_text())
    assert gate.differences(bandit_eval.evaluate(), committed, 1e-6) == []
    m = {k: v["metrics"] for k, v in committed["models"].items()}
    cand = m["candidate"]
    for base in ("live_ranker", "impact"):
        assert cand["acceptance"] > m[base]["acceptance"]
        assert cand["regret"] < m[base]["regret"]
    assert all(v["largest_slate"] <= bandit.CAP for v in m.values())
    assert cand["ope_ips_error"] < 0.03 and cand["ope_snips_error"] < 0.03
