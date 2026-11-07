"""#77: POST /api/sync and the sync lock. Fake transport, in-memory Keychain,
synthetic data; no network."""

import logging
import secrets
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, identity
from finance_agent.cli import bank
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger
from finance_agent.storage import default_db_path, keychain, open_db
from finance_agent.storage.lock import Busy, exclusive
from synthetic_lloyds import generate_histories
from synthetic_simplefin import response

H = generate_histories(3, 30)[0]
SERVICE = keychain.SIMPLEFIN_ACCESS_SERVICE
ACCESS = (
    f"https://{secrets.token_hex(6)}:{secrets.token_hex(12)}@{simplefin.HOST}/simplefin"
)

Env = tuple[TestClient, "Transport", Engine]


class Transport:
    def __init__(self) -> None:
        self.calls = 0
        self.fail: Exception | None = None

    def __call__(
        self, method: str, url: str, headers: dict[str, str], data: bytes | None
    ) -> tuple[int, bytes]:
        self.calls += 1
        if self.fail:
            raise self.fail
        return (200, response(H, 0, 10))


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db" / "ledger.db"))
    t = Transport()
    monkeypatch.setattr(simplefin, "transport", t)
    engine = open_db()
    keychain.set_text(SERVICE, ACCESS)
    with TestClient(api.create_app(engine)) as c:
        yield c, t, engine
    keychain.delete(SERVICE)
    engine.dispose()


def test_sync_returns_counts_for_the_current_user(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _, engine = env
    monkeypatch.setattr(api, "current_user", lambda: 7)
    r = c.post("/api/sync")
    assert r.status_code == 200
    assert r.json()["added"] == 10 and r.json()["matched"] == 0
    with engine.begin() as conn:
        assert len(Ledger(conn, 7).transactions()) == 10
        assert Ledger(conn, identity.current_user()).transactions() == []
        assert Ledger(conn, 7).sync_status().last_sync_at is not None
    assert c.post("/api/sync").json()["added"] == 0


def test_sync_refuses_an_unencrypted_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, engine: Engine
) -> None:
    plain = tmp_path / "plain.db"
    con = sqlite3.connect(plain)
    con.execute("create table t (x)")
    con.commit()
    con.close()
    monkeypatch.setenv("FA_DB_PATH", str(plain))
    t = Transport()
    monkeypatch.setattr(simplefin, "transport", t)
    with TestClient(api.create_app(engine)) as c:
        assert c.post("/api/sync").status_code == 409
    assert t.calls == 0


def test_a_running_sync_makes_the_next_one_skip(env: Env) -> None:
    c, t, _ = env
    data = default_db_path().parent
    with exclusive(data, bank.SYNC_LOCK):
        r = c.post("/api/sync")
        with pytest.raises(Busy), exclusive(data, bank.SYNC_LOCK):
            pass
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    assert t.calls == 0
    assert c.post("/api/sync").status_code == 200  # released afterwards


def test_not_connected_is_a_calm_409(env: Env) -> None:
    c, t, _ = env
    keychain.delete(SERVICE)
    assert c.post("/api/sync").status_code == 409
    assert t.calls == 0


def test_errors_leak_no_secret_and_no_amount(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    c, t, _ = env
    t.fail = OSError(f"boom {ACCESS} £1,234.56")
    with caplog.at_level(logging.DEBUG):
        r = c.post("/api/sync")
    assert r.status_code == 502
    shown = r.text + caplog.text
    assert ACCESS not in shown and "1,234" not in shown
    assert r.json()["detail"] == "Sync failed (SimpleFINError)."
    assert c.get("/api/sync-status").json()["failure_class"] == "SimpleFINError"


def test_cli_sync_skips_while_another_run_holds_the_lock(
    env: Env, capsys: pytest.CaptureFixture[str]
) -> None:
    from finance_agent.cli import main

    _, t, _ = env
    with exclusive(default_db_path().parent, bank.SYNC_LOCK):
        assert main(["sync", "--notify"]) == 0
    assert "skipped" in capsys.readouterr().out and t.calls == 0
