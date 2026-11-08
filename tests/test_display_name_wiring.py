"""#223: display_name beside the stored Merchant on Transactions, the Review Queue,
Insights and the Assistant's tool output. Display only: stored text is unchanged."""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.categorise.display_names import display_name
from finance_agent.engine import insights
from finance_agent.ledger import Ledger
from finance_agent.ledger.insights import Found
from finance_agent.storage import Scoped, schema
from synthetic_assistant import ME, OTHER, seed
from test_api_review_queue import seed as seed_queue
from test_assistant_injection import OBFUSCATED, calls, fake, tool_results, turn

RAW = "ANTHROPIC CLAUDE SUB ANTHROPIC CLAUDE"


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_transactions_carry_display_name_and_only_mine(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed(engine, ME, tmp_path)
    seed(engine, OTHER, tmp_path, rows=[(2, "DEB", "OTHERSHOP 9911", -8800)])
    items = client.get("/api/transactions").json()["items"]
    shown = [i for i in items if i["merchant"]]
    assert shown
    assert all(i["display_name"] == display_name(i["merchant"]) for i in shown)
    assert "OTHERSHOP" not in json.dumps(items).upper()


def test_review_queue_title_has_a_display_name_and_stays_stored(
    engine: Engine, tmp_path: Path, client: TestClient
) -> None:
    seed_queue(engine, tmp_path)
    seed_queue(engine, tmp_path, user_id=2)
    cafe, _ = client.get("/api/review-queue").json()["items"]
    assert (cafe["title"], cafe["display_name"]) == ("SYNTH CAFE", "Synth Cafe")


def test_insight_explanation_uses_the_readable_name_but_a_category_stays() -> None:
    def found(type: str, key: str) -> Found:
        return Found(type, key, "Software", date(2026, 1, 1), 12000, 80, ())

    assert insights.explain(found("recurring", RAW)).startswith("Claude (Anthropic) ")
    assert insights.shown_key(found("category_spike", "Eating out")) == "Eating out"


def test_assistant_tools_use_display_name_through_the_sanitiser(
    engine: Engine, tmp_path: Path
) -> None:
    seed(engine, ME, tmp_path)
    with engine.begin() as conn:
        a = Scoped(conn, ME).insert(schema.merchant, name=RAW)
        b = Scoped(conn, ME).insert(schema.merchant, name=OBFUSCATED["plain"])
        led = Ledger(conn, ME)
        led.add_subscription(a, "subscription", "AI tools")
        led.add_subscription(b, "subscription", "Streaming")
    m = fake("ask", calls(("list_subscriptions", {})), "ok")
    turn(engine, m, "what subscriptions do I have?")
    rows = {
        r["evidence_id"]: r["merchant"]["data"]
        for r in json.loads(tool_results(m)[0])["rows"]
    }
    assert rows[f"sub:{a}"] == "Claude (Anthropic)"
    assert "ignore" not in rows[f"sub:{b}"].lower()
