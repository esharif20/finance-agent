"""Allocation Plans (#267): `allocation_plan` and GET /api/plan/allocation give
the same engine figures, honour confirmed constraints, explain an infeasible
one, never leak another user's data and write nothing. Synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.assistant import ToolCall
from finance_agent.assistant.tools import run, specs
from finance_agent.ledger.proposals import MemoryDraft, Proposals
from test_api_cash_flow import _load  # pyright: ignore[reportPrivateUsage]

URL = "/api/plan/allocation"
Export = Callable[[str], Path]


def remember(engine: Engine, **fields: Any) -> None:
    text = fields["text"]
    draft = MemoryDraft.model_validate(
        {
            "provenance": {"turn_id": "t1", "quote": text, "said_on": date.today()},
            **fields,
        }
    )
    with engine.begin() as conn:
        props = Proposals(conn, 1)
        props.confirm(props.propose_memory(draft).id)


def get(engine: Engine) -> dict[str, Any]:
    with TestClient(api.create_app(engine)) as c:
        return c.get(URL).json()


def total(lines: list[dict[str, Any]]) -> int:
    return sum(ln["amount"]["minor"] for ln in lines)


def test_the_plan_sums_to_income_beside_a_baseline(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    body = get(engine)
    assert body["status"] == "ok", body
    assert total(body["lines"]) == total(body["baseline"]) == body["income"]["minor"]
    assert any(ln["kind"] == "category" for ln in body["lines"]), body


def test_the_tool_and_the_api_agree(engine: Engine, write_export: Export) -> None:
    _load(engine, write_export, 1, 14)
    with engine.begin() as conn:
        out = run("plan", ToolCall(name="allocation_plan", arguments={}), conn)
    assert out.model_dump(mode="json") == get(engine)


def test_a_goal_is_planned_and_a_floor_is_held(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    due = date.today() + timedelta(days=62)
    remember(
        engine,
        type="goal",
        text="Holiday fund",
        tags=("holiday",),
        amount_minor=120_000,
        due_on=due,
    )
    remember(
        engine,
        type="constraint",
        text="Put at least £1,300 a month into the holiday fund",
        tags=("holiday",),
        amount_minor=130_000,
    )
    body = get(engine)
    goal = next(ln for ln in body["lines"] if ln["kind"] == "goal")
    assert goal["amount"]["minor"] >= 130_000, body
    assert total(body["lines"]) == body["income"]["minor"]


def test_an_impossible_constraint_explains_and_gives_no_plan(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    remember(
        engine,
        type="goal",
        text="Car",
        tags=("car",),
        amount_minor=99_000_000,
        due_on=date.today() + timedelta(days=90),
    )
    remember(
        engine,
        type="constraint",
        text="Put at least £9,000,000 a month into the car",
        tags=("car",),
        amount_minor=900_000_000,
    )
    body = get(engine)
    assert body["status"] == "infeasible", body
    assert body["lines"] == [] and body["infeasible_reason"]


def test_no_income_history_is_insufficient(engine: Engine) -> None:
    body = get(engine)
    assert body["status"] == "insufficient" and body["lines"] == []


def test_another_users_history_never_plans_for_this_one(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 2, 14)
    assert get(engine)["status"] == "insufficient"


def test_the_tool_is_plan_only_and_has_no_payment_field() -> None:
    (spec,) = [s for s in specs("plan") if s.name == "allocation_plan"]
    assert spec.parameters.get("properties", {}) == {}
    assert spec.parameters.get("additionalProperties") is False
    assert "allocation_plan" not in {s.name for s in specs("ask")}
