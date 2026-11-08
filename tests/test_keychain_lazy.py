"""The Gemini key is read from the Keychain only when a thread uses the cloud,
at most once per process: never per request, at startup or to ask whether the
cloud is set up (that is a non-secret flag file). macOS prompts on each read.
Synthetic data only; the Keychain is conftest's in-memory fake."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import keyring
import keyring.backend
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.api import chat
from finance_agent.assistant import FakeChatModel
from finance_agent.cli import keys
from finance_agent.storage import keychain
from synthetic_assistant import ME, seed

TODAY = date(2026, 1, 20)


class Counting(keyring.backend.KeyringBackend):
    """Counts reads; with `fail`, any read of the Gemini item fails the test."""

    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self, fail: bool, inner: Any) -> None:
        self.fail, self.inner = fail, inner
        self.reads = 0
        self.items: dict[tuple[str, str], str] = {
            (keychain.GEMINI_SERVICE, keychain.GEMINI_ACCOUNT): "synthetic-key"
        }

    def get_password(self, service: str, username: str) -> str | None:
        if service == keychain.GEMINI_SERVICE:
            self.reads += 1
            if self.fail:
                raise AssertionError("the Gemini key was read with the cloud off")
            return self.items.get((service, username))
        return self.inner.get_password(service, username)  # the database keys

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.items.pop((service, username), None)


@pytest.fixture
def ring(request: pytest.FixtureRequest) -> Iterator[Counting]:
    held = keyring.get_keyring()
    k = Counting(getattr(request, "param", True), held)
    keyring.set_keyring(k)
    keychain.forget_gemini()
    try:
        yield k
    finally:
        keyring.set_keyring(held)
        keychain.forget_gemini()
        chat.LazyCloud._model = None  # pyright: ignore[reportPrivateUsage]


def _db(engine: Engine) -> Path:
    return Path(str(engine.url.database))


def _client(engine: Engine) -> TestClient:
    app = api.create_app(engine)
    app.dependency_overrides[chat.today] = lambda: TODAY
    app.dependency_overrides[chat.chat_model] = lambda: FakeChatModel([])
    return TestClient(app)


@pytest.mark.parametrize("configured", [False, True])
def test_chat_turns_never_read_the_key_when_the_cloud_is_off(
    engine: Engine, tmp_path: Path, ring: Counting, configured: bool
) -> None:
    seed(engine, ME, tmp_path)
    keychain.mark_gemini(_db(engine), configured)
    with _client(engine) as c:
        for _ in range(3):  # each one used to prompt
            r = c.post(
                "/api/chat", json={"message": "How much did I spend in January 2026?"}
            )
            assert r.status_code == 200
            assert "event: done" in r.text
    assert ring.reads == 0


def test_whether_the_cloud_is_set_up_is_the_flag_not_the_keychain(
    engine: Engine, ring: Counting
) -> None:
    req: Any = type(
        "R", (), {"app": type("A", (), {"state": type("S", (), {"engine": engine})})}
    )()
    assert chat.cloud_route(req) is None
    keychain.mark_gemini(_db(engine), True)
    assert chat.cloud_route(req) is not None
    keychain.mark_gemini(_db(engine), False)
    assert chat.cloud_route(req) is None
    assert ring.reads == 0


@pytest.mark.parametrize("ring", [False], indirect=True)
def test_a_cloud_call_reads_the_key_once_per_process(
    ring: Counting, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[str] = []

    class Fake:
        def __init__(self, key: str) -> None:
            made.append(key)

        def complete(self, payload: Any, *, model: str, max_tokens: int) -> Any:
            return "ok", None

    monkeypatch.setattr(chat, "CloudChatModel", Fake)
    for _ in range(3):
        assert chat.LazyCloud().complete(Any, model="m", max_tokens=1)[0] == "ok"  # pyright: ignore[reportArgumentType]
    assert ring.reads == 1
    assert made == ["synthetic-key"]


@pytest.mark.parametrize("ring", [False], indirect=True)
def test_fa_keys_set_and_remove_keep_the_flag(ring: Counting) -> None:
    from finance_agent.storage import default_db_path

    db = default_db_path()  # conftest's temporary path, never the real one
    assert keys.set_key("gemini", lambda _p: "synthetic-key-2") == 0
    assert keychain.gemini_configured(db)
    assert keys.remove("gemini", lambda _p: "gemini") == 0
    assert not keychain.gemini_configured(db)
