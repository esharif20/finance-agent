"""#27: `fa connect`, `fa disconnect` and `fa sync`, in process, with a fake
transport and the in-memory Keychain. No network, no real Keychain."""

import base64
import json
import logging
import secrets
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import keyring
import pytest

from finance_agent.cli import main
from finance_agent.ingest import simplefin
from finance_agent.storage import keychain
from synthetic_lloyds import generate_histories, render_export
from synthetic_simplefin import response

H = generate_histories(3, 30)[0]
SERVICE = keychain.SIMPLEFIN_ACCESS_SERVICE


class Transport:
    """Answers the claim POST with the access URL and each GET with `body`."""

    def __init__(self, access: str, body: bytes) -> None:
        self.access, self.body = access, body
        self.calls: list[tuple[str, str]] = []
        self.fail: Exception | None = None

    def __call__(
        self, method: str, url: str, headers: dict[str, str], data: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url))
        if self.fail:
            raise self.fail
        return (200, self.access.encode() if method == "POST" else self.body)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "db" / "ledger.db"))
    access = f"https://{secrets.token_hex(6)}:{secrets.token_hex(12)}@{simplefin.HOST}/simplefin"
    claim_url = f"https://{simplefin.HOST}/claim/{secrets.token_hex(10)}"
    token = base64.b64encode(claim_url.encode()).decode()
    t = Transport(access, response(H, 0, 10))
    monkeypatch.setattr(simplefin, "transport", t)
    answers: list[str] = []

    def hidden(prompt: str = "") -> str:
        return token

    def typed(prompt: str = "") -> str:
        return answers.pop(0) if answers else ""

    monkeypatch.setattr("getpass.getpass", hidden)
    monkeypatch.setattr("builtins.input", typed)
    yield {
        "access": access,
        "claim": claim_url,
        "token": token,
        "t": t,
        "answers": answers,
    }
    keyring.delete_password(SERVICE, keychain.ACCOUNT)


def test_connect_stores_the_access_url_in_the_keychain_only(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect"]) == 0
    assert keyring.get_password(SERVICE, keychain.ACCOUNT) == env["access"]
    assert [m for m, _ in env["t"].calls] == ["POST", "GET"]
    assert "Synthetic Current" in capsys.readouterr().out
    assert main(["connect"]) == 1  # already connected: claim is never re-run
    assert len(env["t"].calls) == 2


def test_connect_links_a_synced_account_to_its_csv_account(
    env: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = tmp_path / "export.csv"
    csv.write_text(render_export(H, 0, 10).text)
    assert main(["import", str(csv)]) == 0
    env["answers"].append("••••0001")
    assert main(["connect"]) == 0
    assert main(["sync"]) == 0
    out = capsys.readouterr().out
    assert "0 added, 10 matched to CSV rows" in out
    assert main(["summary", "2026-01"]) == 0


def test_sync_adds_rows_and_a_second_sync_adds_nothing(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect"]) == 0
    capsys.readouterr()
    assert main(["sync"]) == 0
    assert "10 added" in capsys.readouterr().out
    assert main(["sync"]) == 0
    assert "0 added" in capsys.readouterr().out
    assert all(m == "POST" or "start-date" in u for m, u in env["t"].calls)


def test_sync_without_connect_says_so(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["sync"]) == 1
    assert "fa connect" in capsys.readouterr().err
    assert env["t"].calls == []


def test_disconnect_removes_the_keychain_item_and_explains_revoking(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect"]) == 0
    assert main(["disconnect"]) == 0
    assert keyring.get_password(SERVICE, keychain.ACCOUNT) is None
    assert "revoke" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("command", ["connect", "sync"])
def test_refuses_an_unencrypted_database(
    env: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    import sqlite3

    plain = tmp_path / "plain" / "ledger.db"
    plain.parent.mkdir()
    sqlite3.connect(plain).execute("CREATE TABLE x (y)").connection.commit()
    keyring.set_password(SERVICE, keychain.ACCOUNT, env["access"])
    monkeypatch.setenv("FA_DB_PATH", str(plain))
    assert main([command]) == 1
    assert env["t"].calls == []


def test_secrets_never_reach_logs_output_or_errors(
    env: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    assert main(["connect"]) == 0
    assert main(["sync"]) == 0
    env["t"].fail = OSError(f"connection to {env['access']} {env['claim']} failed")
    assert main(["sync"]) == 1
    env["t"].body = b'{"accounts": "' + env["access"].encode() + b'"}'
    env["t"].fail = None
    assert main(["sync"]) == 1
    out = capsys.readouterr()
    seen = out.out + out.err + caplog.text
    access = env["access"]
    for secret in (access, access.split("@")[0], env["claim"], env["token"]):
        assert secret not in seen
    for desc in {t.description for t in H.txns[:10]}:
        assert desc not in caplog.text


def test_sync_refuses_a_response_it_cannot_read_and_stores_nothing(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect"]) == 0
    env["t"].body = json.dumps({"accounts": [{"id": 1}]}).encode()
    assert main(["sync"]) == 1
    env["t"].body = response(H, 0, 10)
    capsys.readouterr()
    assert main(["sync"]) == 0
    assert "10 added" in capsys.readouterr().out


def test_a_failing_claim_leaks_nothing_to_output_logs_or_files(
    env: dict[str, Any],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    env["t"].fail = OSError(f"POST {env['claim']} -> {env['access']} failed")
    assert main(["connect"]) == 1
    assert keyring.get_password(SERVICE, keychain.ACCOUNT) is None
    env["t"].fail = None
    assert main(["connect"]) == 0
    assert main(["sync"]) == 0
    out = capsys.readouterr()
    seen = (out.out + out.err + caplog.text).encode()
    seen += b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    access = env["access"]
    for secret in (access, access.split("@")[0], env["claim"], env["token"]):
        assert secret.encode() not in seen
