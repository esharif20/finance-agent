"""The Assistant's model setting: Faster or More careful, per user. Changing it
unloads the old model. Synthetic only; nothing here talks to Ollama."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from finance_agent import api
from finance_agent.api import chat
from finance_agent.assistant.ollama import DEFAULT_CHOICE, MODELS, OllamaChatModel
from finance_agent.ledger.settings import Settings

ME, OTHER = 1, 2
OTHER_CHOICE = "careful" if DEFAULT_CHOICE == "fast" else "fast"


def test_each_user_has_their_own_choice_and_the_default_until_they_pick(
    engine: Engine,
) -> None:
    with engine.begin() as conn:
        for u in (ME, OTHER):
            conn.execute(
                text("INSERT OR IGNORE INTO user (id, created_at) VALUES (:u, :t)"),
                {"u": u, "t": "2026-01-01"},
            )
        assert Settings(conn, ME).assistant_model() is None  # never chose
        Settings(conn, ME).set_assistant_model("careful")
        Settings(conn, ME).set_assistant_model("fast")  # replaces, never a 2nd row
        Settings(conn, OTHER).set_assistant_model("careful")
        assert Settings(conn, ME).assistant_model() == "fast"
        assert Settings(conn, OTHER).assistant_model() == "careful"
        with pytest.raises(ValueError):
            Settings(conn, ME).set_assistant_model("huge")  # pyright: ignore[reportArgumentType]


@pytest.fixture
def client(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    unloaded: list[str] = []
    monkeypatch.setattr(chat, "unload", unloaded.append)
    with TestClient(api.create_app(engine)) as c:
        c.unloaded = unloaded  # type: ignore[attr-defined]
        yield c


def unloaded(c: TestClient) -> list[str]:
    return c.unloaded  # type: ignore[attr-defined]


def test_the_setting_reads_and_changes_through_the_api_and_unloads_the_old_model(
    client: TestClient,
) -> None:
    assert client.get("/api/chat/model").json() == {"model": DEFAULT_CHOICE}
    r = client.put("/api/chat/model", json={"model": OTHER_CHOICE})
    assert (r.status_code, r.json()) == (200, {"model": OTHER_CHOICE})
    assert unloaded(client) == [MODELS[DEFAULT_CHOICE]]
    assert client.get("/api/chat/model").json() == {"model": OTHER_CHOICE}
    client.put("/api/chat/model", json={"model": OTHER_CHOICE})  # unchanged
    assert len(unloaded(client)) == 1


@pytest.mark.parametrize(
    "body", [{"model": "qwen3.5:70b"}, {"model": "fast", "x": 1}, {}, ["fast"]]
)
def test_only_a_known_choice_is_accepted(client: TestClient, body: Any) -> None:
    r = client.put("/api/chat/model", json=body)
    assert r.status_code == 422 and "70b" not in r.text  # never echoed
    assert unloaded(client) == []


def test_the_chat_uses_the_chosen_model(client: TestClient, engine: Engine) -> None:
    client.put("/api/chat/model", json={"model": "careful"})
    with engine.begin() as conn:
        model = chat.model_for(conn)
    assert isinstance(model, OllamaChatModel) and model.model == MODELS["careful"]
