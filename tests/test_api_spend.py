"""GET /api/spend and GET /api/gateway/payloads (#295): read-only, pence as
integers, payloads for the current user only."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.gateway import cost
from finance_agent.gateway.cloud import Usage
from finance_agent.storage.spend import SpendStore


def _store(engine: Engine) -> SpendStore:
    return SpendStore.beside(Path(engine.url.database or ""))


def test_spend_reports_each_bucket(engine: Engine) -> None:
    s = _store(engine)
    r = s.reserve("gateway_chat", "gemini-flash-latest", 0, 10)
    assert isinstance(r, cost.Allowed)
    s.settle(r.reservation, Usage(0, 0, 130_000, "gemini-3.8-flash"))
    with TestClient(api.create_app(engine)) as c:
        got = c.get("/api/spend").json()
    assert got == [
        {
            "bucket": "synthetic_judge",
            "cap_pence": 150,
            "spent_pence": 0,
            "reserved_pence": 0,
            "state": "ok",
        },
        {
            "bucket": "gateway_chat",
            "cap_pence": 100,
            "spent_pence": 83,
            "reserved_pence": 0,
            "state": "warn",
        },
        {
            "bucket": "briefing",
            "cap_pence": 100,
            "spent_pence": 0,
            "reserved_pence": 0,
            "state": "ok",
        },
    ]


def test_payloads_are_the_current_users_only(engine: Engine) -> None:
    s = _store(engine)
    s.log_payload(1, "c1", {"intent": "a", "question": "{C1}?"}, "m", 2)
    s.log_payload(2, "c1", {"intent": "b", "question": "{C2}?"}, "m", 3)
    with TestClient(api.create_app(engine)) as c:
        got = c.get("/api/gateway/payloads", params={"conversation": "c1"}).json()
        assert c.get("/api/gateway/payloads").status_code == 422
        assert c.post("/api/spend").status_code == 405
    assert [(p["payload"]["intent"], p["cost_pence"]) for p in got] == [("a", 2)]
