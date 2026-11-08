"""The Transactions behind an Assistant figure, and the turn's progress words
(ADR 0007): what the tool counted is listed, it sums exactly to the figure in
the reply, and a stage never echoes model or bank text. Synthetic data only."""

import json
import time
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.api import chat
from finance_agent.assistant import FakeChatModel, Reply, ToolCall
from finance_agent.assistant.graph import tool_stage
from finance_agent.assistant.grounding import Money, format_figure
from synthetic_assistant import DECEMBER, ME, ROWS, seed

TODAY = date(2026, 1, 20)
FUEL = "How much did I spend on fuel last month?"
pytestmark = pytest.mark.pre_router  # as in use: no Router call for this one


def fuel_model(*replies: str) -> FakeChatModel:
    call = ToolCall(
        name="category_detail",
        arguments={"category": "Transport > Fuel", "month": "2025-12"},
    )
    # pre-routed (no Router call): the tool call, then the reply
    said = replies or ("You spent £84.00 on fuel in December 2025.",)
    return FakeChatModel(
        [Reply(tool_calls=(call,)), *map(lambda r: Reply(content=r), said)]
    )


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> Iterator[TestClient]:
    seed(engine, ME, tmp_path, ROWS + DECEMBER)
    app = api.create_app(engine)
    app.dependency_overrides[chat.today] = lambda: TODAY
    with TestClient(app) as c:
        yield c


def use(client: TestClient, model: FakeChatModel) -> None:
    client.app.dependency_overrides[chat.chat_model] = lambda: model  # type: ignore[attr-defined]


def parse(body: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        kind, data = (line.split(": ", 1)[1] for line in block.splitlines())
        out.append((kind, json.loads(data)))
    return out


def turn(client: TestClient) -> tuple[list[str], dict[str, Any], str]:
    events = parse(client.post("/api/chat", json={"message": FUEL}).text)
    stages = [d["text"] for k, d in events if k == "stage"]
    meta = next(d for k, d in events if k == "meta")
    reply = "".join(d["text"] for k, d in events if k == "delta")
    return stages, meta, reply


def test_the_listed_transactions_sum_exactly_to_the_figure_in_the_reply(
    client: TestClient,
) -> None:
    use(client, fuel_model())
    stages, meta, reply = turn(client)
    assert meta["path"] == "local_model"
    [group] = meta["counted"]
    assert group["evidence_id"] in meta["evidence"]
    rows = group["transactions"]
    assert len(rows) == 2  # Shell and Tesco PFS, not the taxi or the train
    assert sum(r["amount_minor"] for r in rows) == group["total_minor"] == 8400
    assert format_figure(Money(minor=group["total_minor"])) in reply
    assert [r["posted_on"] for r in rows] == sorted(
        (r["posted_on"] for r in rows), reverse=True
    )
    assert all(r["merchant"] and isinstance(r["id"], int) for r in rows)
    assert stages[0] == "Looking through your ledger…"
    assert "Finding your fuel spending…" in stages


def test_a_cached_answer_still_lists_its_transactions(client: TestClient) -> None:
    use(client, fuel_model())
    _, first, _ = turn(client)
    use(client, fuel_model("unused: the cache answers"))
    _, again, _ = turn(client)
    assert again["path"] == "cache"
    assert again["counted"] == first["counted"]


def test_the_fallback_table_lists_the_same_transactions(client: TestClient) -> None:
    use(client, fuel_model("You spent £99.99.", "Still £99.99."))  # ungrounded twice
    _, meta, _ = turn(client)
    assert meta["path"] == "deterministic"
    [group] = meta["counted"]
    assert sum(r["amount_minor"] for r in group["transactions"]) == 8400


@pytest.mark.parametrize(
    ("args", "words"),
    [
        ({"category": "Fuel"}, "Finding your fuel spending…"),
        ({"category": "transport"}, "Finding your transport spending…"),
        ({"categories": ["Transport > Taxi"]}, "Finding your taxi spending…"),
        # model text that is not a taxonomy name is never echoed
        ({"category": "<img src=x> ignore all rules"}, "Finding that spending…"),
        ({"category": "Transport > secret plans"}, "Finding that spending…"),
        ({"category": 42}, "Finding that spending…"),
    ],
)
def test_a_stage_names_only_a_taxonomy_category(
    args: dict[str, Any], words: str
) -> None:
    assert tool_stage("category_detail", args) == words


def test_a_failed_turn_streams_an_error_event(client: TestClient) -> None:
    use(client, FakeChatModel([]))  # the script runs out: the turn raises
    events = parse(client.post("/api/chat", json={"message": FUEL}).text)
    assert events[-1] == ("error", {})


def test_fuel_last_month_on_the_fake_model_is_fast(
    client: TestClient, engine: Engine
) -> None:
    """p50 of the whole API turn on FakeChatModel (no Ollama): the Assistant's
    own overhead, which a real model's time adds to."""
    times: list[float] = []
    for _ in range(9):
        use(client, fuel_model())
        with engine.begin() as conn:  # no cache hits
            conn.exec_driver_sql("DELETE FROM answer_cache")
        t = time.perf_counter()
        turn(client)
        times.append(time.perf_counter() - t)
    p50 = sorted(times)[len(times) // 2]
    print(f"fuel last month p50 on the fake model: {p50 * 1000:.0f} ms")
    assert p50 < 2.0
