"""Coaching goals (#278): GET /api/goals gives the engine's figures, a check-in
only on a projected payday in the user's tone, a linked Account's progress,
and never another user's goals. Synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, update

from finance_agent import api
from finance_agent.engine.forecast import cash_flow
from finance_agent.ledger import Ledger
from finance_agent.ledger.proposals import MemoryDraft, Proposals
from finance_agent.storage import schema
from test_api_cash_flow import _load  # pyright: ignore[reportPrivateUsage]

URL = "/api/goals"
Export = Callable[[str], Path]


def remember(engine: Engine, user: int = 1, **fields: Any) -> None:
    text = fields["text"]
    draft = MemoryDraft.model_validate(
        {
            "provenance": {"turn_id": "t1", "quote": text, "said_on": date.today()},
            **fields,
        }
    )
    with engine.begin() as conn:
        props = Proposals(conn, user)
        props.confirm(props.propose_memory(draft).id)


def get(engine: Engine, **params: str) -> dict[str, Any]:
    with TestClient(api.create_app(engine)) as c:
        return c.get(URL, params=params).json()


def payday(engine: Engine) -> date:
    with engine.begin() as conn:
        flow = cash_flow(Ledger(conn, 1), date.today(), 1)
    return min(d for p in flow.income for d in p.dates)


def savings(engine: Engine) -> None:
    """The check-in names the savings balance, so it needs a savings Account."""
    with engine.begin() as conn:
        conn.execute(update(schema.account).values(kind="savings"))


def goal(due_in: int, amount: int = 100_000, **extra: Any) -> dict[str, Any]:
    return {
        "type": "goal",
        "text": "Holiday fund",
        "amount_minor": amount,
        "due_on": date.today() + timedelta(days=due_in),
        **extra,
    }


def test_the_weekly_target_is_the_engines_and_estimated(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    remember(engine, **goal(70))
    (g,) = get(engine)["goals"]
    assert g["estimated"] is True and g["linked_account_id"] is None
    assert g["weeks_left"] == 10 and g["weekly_target_minor"] == 10_000
    assert g["remaining_minor"] == 100_000 - g["saved_minor"]
    assert g["status"] in {"at_risk", "on_track", "unchecked"}


def test_a_linked_account_gives_real_progress(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    with engine.begin() as conn:
        acc = Ledger(conn, 1).accounts()[0].id
    remember(engine, **goal(70, linked_account_id=acc))
    (g,) = get(engine)["goals"]
    assert g["estimated"] is False and g["linked_account_id"] == acc


def test_the_checkin_appears_only_on_a_projected_payday(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    savings(engine)
    remember(engine, **goal(120))
    pd = payday(engine)
    on = get(engine, on=pd.isoformat())
    off = get(engine, on=(pd + timedelta(days=1)).isoformat())
    assert on["payday"] is True and on["checkin"]["title"]
    assert off["payday"] is False and off["checkin"] is None


def test_the_checkin_follows_the_users_tone(
    engine: Engine, write_export: Export
) -> None:
    _load(engine, write_export, 1, 14)
    savings(engine)
    pd = payday(engine).isoformat()
    gentle = get(engine, on=pd)["checkin"]["title"]
    remember(engine, type="preference", text="Be blunt", tags=("tone", "blunt"))
    assert get(engine, on=pd)["checkin"]["title"] != gentle


def test_another_users_goals_never_show(engine: Engine, write_export: Export) -> None:
    _load(engine, write_export, 2, 14)
    remember(engine, 2, **goal(30, 9_000))
    assert get(engine)["goals"] == []
