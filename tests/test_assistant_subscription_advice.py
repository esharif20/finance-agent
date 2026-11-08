"""`subscription_advice` (#308): in Ask only, the cap from a confirmed
constraint Memory, monthly costs equal to /api/subscriptions/costs, every
figure a tool value, and nothing cancelled. Synthetic data only."""

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.assistant import ToolCall
from finance_agent.assistant.grounding import Money
from finance_agent.assistant.tools import ToolNotAllowed, run
from finance_agent.assistant.tools.advice import GROUPS, Group
from finance_agent.assistant.values import tool_values
from finance_agent.ledger import Ledger
from finance_agent.ledger.proposals import MemoryDraft, Proposals
from test_api_subscription_costs import charges, seed_rows

Export = Callable[[str], Path]
AI_ROWS = (
    charges("OPENAI CHATGPT", ["20.00"] * 4)
    + charges("ANTHROPIC CLAUDE", ["18.00"] * 4, last_days_ago=5)
    + charges("CURSOR", ["16.00"] * 4, last_days_ago=3)
    + charges("NETFLIX.COM", ["10.99"] * 4)
)


def cap(engine: Engine, pence: int, text: str, user: int = 1) -> None:
    draft = MemoryDraft.model_validate(
        {
            "type": "constraint",
            "text": text,
            "tags": ("subscriptions",),
            "provenance": {"turn_id": "t1", "quote": text, "said_on": date.today()},
            "amount_minor": pence,
        }
    )
    with engine.begin() as conn:
        props = Proposals(conn, user)
        props.confirm(props.propose_memory(draft).id)


def advice(engine: Engine, subgraph: str = "ask", **args: Any) -> Any:
    with engine.begin() as conn:
        call = ToolCall(name="subscription_advice", arguments=args)
        return run(subgraph, call, conn)


def test_the_group_list_is_the_taxonomy() -> None:
    assert set(get_args(Group)) == set(GROUPS)


def test_options_under_the_cap_with_figures_equal_to_the_costs_api(
    engine: Engine, write_export: Export
) -> None:
    seed_rows(engine, write_export, AI_ROWS)
    cap(engine, 4000, "Cap my AI services at £40 a month")
    out = advice(engine, group="AI tools")
    assert out.status == "ok" and out.over_cap
    assert out.cap.minor == 4000 and out.current_total.minor == 5400
    with TestClient(api.create_app(engine)) as c:
        body = c.get("/api/subscriptions/costs").json()
    monthly = {
        f"sub:{i['merchant_id']}": i["monthly"]["minor"]
        for i in body["items"]
        if i["group"] == "AI tools"
    }
    assert len(monthly) == 3
    assert {"AI tools": 5400} == {
        g["group"]: g["monthly"]["minor"]
        for g in body["groups"]
        if g["group"] == "AI tools"
    }
    assert 2 <= len(out.options) <= 3
    for o in out.options:
        assert {s.evidence_id: s.monthly.minor for s in o.steps} == monthly
        kept = [s.monthly.minor for s in o.steps if s.action != "cancel"]
        assert o.new_total.minor == sum(kept) <= 4000
        assert o.saving.minor == 5400 - o.new_total.minor
    con = next(o for o in out.options if "consolidate" in (o.name, *o.also))
    chats = [s for s in con.steps if s.monthly.minor != 1600]  # not Cursor
    assert sorted(s.action != "cancel" for s in chats) == [False, True]
    # every figure is a tool value the grounding check can match
    minors = {
        v.value.minor
        for v in tool_values("subscription_advice", out)
        if isinstance(v.value, Money)
    }
    assert {4000, 5400, *(o.new_total.minor for o in out.options)} <= minors
    # read-only: nothing was cancelled
    with engine.begin() as conn:
        assert all(
            e.status != "user_cancelled" for e in Ledger(conn, 1).subscriptions()
        )


def test_no_cap_means_no_advice_and_another_users_cap_never_counts(
    engine: Engine, write_export: Export
) -> None:
    seed_rows(engine, write_export, AI_ROWS)
    seed_rows(engine, write_export, AI_ROWS, user_id=2)
    cap(engine, 1000, "Cap my AI tools at £10", user=2)
    cap(engine, 1000, "Cap streaming at £10")  # another group
    out = advice(engine, group="AI tools")
    assert out.status == "no_cap" and out.options == [] and out.cap is None


def test_under_the_cap_and_nothing_fits(engine: Engine, write_export: Export) -> None:
    seed_rows(engine, write_export, AI_ROWS)
    cap(engine, 5000, "streaming budget £50 a month")
    assert advice(engine, group="Streaming").status == "already_under_cap"
    cap(engine, 500, "keep AI under £5 a month")
    out = advice(engine, group="AI tools")
    assert out.status == "nothing_fits" and out.options == []


def test_outside_ask_or_with_bad_arguments_it_is_refused(engine: Engine) -> None:
    for sub in ("plan", "correct", "memory"):
        with pytest.raises(ToolNotAllowed):
            advice(engine, sub, group="AI tools")
    for bad in ({}, {"group": "Rent"}, {"group": "AI tools", "cancel": True}):
        with pytest.raises(ValidationError):
            advice(engine, **bad)
