import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import keyring
import keyring.backend
import pytest
from sqlalchemy import Engine

from finance_agent import storage
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db

REAL_DATA_DIR = storage.DEFAULT_DB.parent


class _LocalModelOff:
    """Import and sync try the local model; tests never reach a real Ollama."""

    model = "nomic-embed-text"

    def digest(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent.categorise import embedding

    monkeypatch.setattr(embedding, "OllamaEmbedder", _LocalModelOff)


@pytest.fixture(autouse=True)
def _model_router_unless_marked(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scripted graph tests start with the Router's reply, so the keyword
    pre-router is off unless a test is marked `pre_router`."""
    if request.node.get_closest_marker("pre_router") is None:
        from finance_agent.assistant import graph

        def model_router(_message: str) -> None:
            return None

        monkeypatch.setattr(graph, "pre_route", model_router)


@pytest.fixture(autouse=True)
def _no_real_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """A test never pops a macOS notification; one that wants them records."""
    from finance_agent import notify
    from finance_agent.cli import watch

    def silent(_: str, __: str) -> None:
        return None

    monkeypatch.setattr(notify, "osascript", silent)
    monkeypatch.setattr(watch, "osascript", silent)


@pytest.fixture(autouse=True)
def _never_touch_real_data(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test (and any subprocess it spawns) defaults to a temp database,
    and opening anything under the real data directory fails the test."""
    monkeypatch.setenv(
        storage.DB_ENV, str(tmp_path_factory.mktemp("data") / "ledger.db")
    )
    inside_repo = storage._inside_repo  # pyright: ignore[reportPrivateUsage]

    def guarded(path: Path) -> bool:
        if path.resolve().is_relative_to(REAL_DATA_DIR.resolve()):
            pytest.fail(f"test reached the real data directory: {REAL_DATA_DIR}")
        return inside_repo(path)

    # no test lists real volumes or runs diskutil; backup tests install a fake
    from finance_agent.cli import backup

    monkeypatch.setattr(backup, "VOLUMES", list)

    monkeypatch.setattr(storage, "_inside_repo", guarded)


class MemoryKeyring(keyring.backend.KeyringBackend):
    """In-memory stand-in for the macOS Keychain; tests never touch the real one."""

    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.items.pop((service, username), None)


# Installed at import, before any test or fixture can reach keyring's default.
keyring.set_keyring(MemoryKeyring())


class EgressBlocked(OSError):
    pass


# ADR 0013: only localhost Ollama and the one SimpleFIN host may be contacted.
ALLOWED_EGRESS = {
    ("localhost", 11434),
    ("127.0.0.1", 11434),
    ("::1", 11434),
    (simplefin.HOST, 443),
}
_getaddrinfo = socket.getaddrinfo
_connect = socket.socket.connect


def _guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    if (host, port) not in ALLOWED_EGRESS:
        raise EgressBlocked(f"egress to {host}:{port} is not allowlisted")
    return _getaddrinfo(host, port, *args, **kwargs)


def _guarded_connect(self: socket.socket, address: Any) -> None:
    if self.family in (socket.AF_INET, socket.AF_INET6) and (
        tuple(address[:2]) not in ALLOWED_EGRESS
    ):
        raise EgressBlocked(f"egress to {address[0]}:{address[1]} is not allowlisted")
    _connect(self, address)


# The CI egress test: any test that reaches another host fails.
socket.getaddrinfo = _guarded_getaddrinfo
socket.socket.connect = _guarded_connect  # type: ignore[method-assign]


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh, migrated temporary database per test."""
    eng = open_db(tmp_path / "db" / "ledger.db")
    yield eng
    eng.dispose()


@pytest.fixture
def ledger_for(engine: Engine) -> Iterator[Callable[[int], Ledger]]:
    """ledger_for(user_id) -> Ledger on the temp database, one shared transaction."""
    with engine.begin() as conn:
        yield lambda user_id: Ledger(conn, user_id)


@pytest.fixture
def write_export(tmp_path: Path) -> Callable[[str], Path]:
    n = 0

    def write(text: str) -> Path:
        nonlocal n
        n += 1
        p = tmp_path / f"export{n}.csv"
        p.write_text(text)
        return p

    return write
