"""#27: `fa connect`, `fa disconnect` and `fa sync`, in process, with a fake
transport and the in-memory Keychain. No network, no real Keychain."""

import base64
import json
import logging
import secrets
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import keyring
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from finance_agent.cli import main
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger
from finance_agent.storage import keychain, open_db
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


def test_connect_adopts_the_spike_item_without_a_token(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keyring.set_password(SERVICE, keychain.LEGACY_ACCOUNT, env["access"])

    def no_prompt(prompt: str = "") -> str:
        raise AssertionError("must not ask for a token")

    monkeypatch.setattr("getpass.getpass", no_prompt)
    assert main(["connect"]) == 0
    assert keyring.get_password(SERVICE, keychain.ACCOUNT) == env["access"]
    assert keyring.get_password(SERVICE, keychain.LEGACY_ACCOUNT) is None
    assert [m for m, _ in env["t"].calls] == ["GET"]  # no claim
    captured = capsys.readouterr()
    assert env["access"] not in captured.out + captured.err
    engine = open_db()
    with engine.begin() as conn:
        st = Ledger(conn, 1).sync_status(datetime.now(simplefin.LONDON).date())
    engine.dispose()
    assert st.consent_renewed_on == datetime.now(simplefin.LONDON).date()


def test_failed_adoption_keeps_the_only_copy_of_the_access_url(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    keyring.set_password(SERVICE, keychain.LEGACY_ACCOUNT, env["access"])

    def boom(service: str, value: str) -> None:
        raise OSError(f"keychain said no about {value}")

    monkeypatch.setattr(keychain, "set_text", boom)
    assert main(["connect"]) == 1
    captured = capsys.readouterr()
    assert env["access"] not in captured.out + captured.err
    # the setup token is already spent, so the legacy item is the only copy
    assert keyring.get_password(SERVICE, keychain.LEGACY_ACCOUNT) == env["access"]
    assert [m for m, _ in env["t"].calls] == []


# --- review fixes (#22) -------------------------------------------------------


def _engine_parameters_error(desc: str, access: str) -> Exception:
    from sqlalchemy.exc import IntegrityError

    return IntegrityError(
        "INSERT INTO txn (description, amount_minor) VALUES (?, ?)",
        {"description": desc, "amount_minor": -123456, "url": access},
        Exception("UNIQUE constraint failed"),
    )


@pytest.mark.parametrize("kind", ["integrity", "keyring"])
def test_unexpected_errors_print_the_class_only_never_data(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    kind: str,
) -> None:
    from keyring.errors import KeyringError

    caplog.set_level(logging.DEBUG)
    assert main(["connect"]) == 0
    desc = H.txns[0].description
    if kind == "integrity":
        err = _engine_parameters_error(desc, env["access"])

        def boom(self: Ledger, raw: bytes) -> Any:
            raise err

        monkeypatch.setattr(Ledger, "apply_sync", boom)
    else:

        def locked(service: str) -> str | None:
            raise KeyringError(f"cannot read {env['access']} for {desc}")

        monkeypatch.setattr(keychain, "get_text", locked)
    capsys.readouterr()
    assert main(["sync"]) == 1
    out = capsys.readouterr()
    seen = out.out + out.err + caplog.text
    for leak in (desc, "1234.56", "123456", env["access"], "Traceback", "INSERT"):
        assert leak not in seen
    assert ("IntegrityError" if kind == "integrity" else "KeyringError") in out.err


def test_the_engine_hides_sql_parameters(tmp_path: Path) -> None:
    engine = open_db(tmp_path / "x" / "ledger.db")
    try:
        assert engine.hide_parameters
    finally:
        engine.dispose()


def test_connect_retries_a_failed_keychain_write_once(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = keychain.set_text
    calls: list[int] = []

    def flaky(service: str, value: str) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("keychain busy")
        real(service, value)

    monkeypatch.setattr(keychain, "set_text", flaky)
    assert main(["connect"]) == 0
    assert keyring.get_password(SERVICE, keychain.ACCOUNT) == env["access"]
    assert len(calls) == 2


def test_connect_says_the_token_is_spent_when_the_keychain_refuses(
    env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from keyring.errors import KeyringError

    def refuse(service: str, value: str) -> None:
        raise KeyringError(f"denied {value}")

    monkeypatch.setattr(keychain, "set_text", refuse)
    assert main(["connect"]) == 1
    out = capsys.readouterr()
    assert env["access"] not in out.out + out.err
    assert "spent" in out.err and "new setup token" in out.err
    assert [m for m, _ in env["t"].calls] == ["POST"]  # nothing fetched after


def test_a_bad_link_answer_rolls_back_the_whole_connect(
    env: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = tmp_path / "export.csv"
    csv.write_text(render_export(H, 0, 10).text)
    assert main(["import", str(csv)]) == 0
    env["answers"].append("not-a-masked-number")
    assert main(["connect"]) == 1
    assert "connect --link" in capsys.readouterr().err
    engine = open_db()
    with engine.begin() as conn:
        st = Ledger(conn, 1).sync_status()
    engine.dispose()
    assert st.consent_renewed_on is None  # the consent renewal rolled back too
    env["answers"].append("••••0001")
    assert main(["connect", "--link"]) == 0
    capsys.readouterr()
    assert main(["sync"]) == 0
    assert "0 added, 10 matched to CSV rows" in capsys.readouterr().out


def test_a_csv_imported_after_connecting_is_linked_with_connect_link(
    env: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect"]) == 0
    assert main(["sync"]) == 0
    csv = tmp_path / "export.csv"
    csv.write_text(render_export(H, 0, 10).text)
    capsys.readouterr()
    assert main(["import", str(csv)]) == 0
    # ADR 0014: not linked yet, so two Accounts, and the user is told
    assert "fa connect --link" in capsys.readouterr().out
    env["answers"].append("••••0001")
    assert main(["connect", "--link"]) == 0
    assert "Linked to ••••0001" in capsys.readouterr().out
    engine = open_db()
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        assert (len(led.accounts()), len(led.transactions())) == (1, 10)
    engine.dispose()
    assert main(["connect", "--link"]) == 0  # nothing left to link: no prompt
    assert env["answers"] == []


def test_connect_link_needs_a_connection(
    env: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["connect", "--link"]) == 1
    assert "fa connect" in capsys.readouterr().err
    assert env["t"].calls == []


@settings(
    max_examples=12,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.integers(0, 2**32 - 1), st.integers(5, 40), st.data(), st.booleans())
def test_csv_and_sync_through_the_cli_never_double_count(
    env: dict[str, Any],
    tmp_path_factory: pytest.TempPathFactory,
    seed: int,
    days: int,
    data: st.DataObject,
    csv_first: bool,
) -> None:
    h = generate_histories(seed, days)[0]
    n = len(h.txns)
    a = data.draw(st.integers(1, n))  # CSV holds rows [0, a), the sync [b, n)
    b = data.draw(st.integers(0, a - 1))
    # Known limit, pinned by test_sync.py::test_identical_purchases_across_the_
    # coverage_boundary_are_never_merged (xfail): ADR 0014 can pair a CSV-only
    # row with a sync-only identical purchase and lose one. Exclude only that.
    csv_only = {(t.amount_minor, t.description) for t in h.txns[:b]}
    assume(not csv_only & {(t.amount_minor, t.description) for t in h.txns[a:]})
    d = tmp_path_factory.mktemp("cli")
    csv = d / "export.csv"
    csv.write_text(render_export(h, 0, a).text)
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FA_DB_PATH", str(d / "db" / "ledger.db"))
        keyring.delete_password(SERVICE, keychain.ACCOUNT)
        env["t"].body = response(h, b, n)
        if csv_first:
            assert main(["import", str(csv)]) == 0
            env["answers"].append("••••0001")
            assert main(["connect"]) == 0
            assert main(["sync"]) == 0
        else:
            assert main(["connect"]) == 0
            assert main(["sync"]) == 0
            assert main(["import", str(csv)]) == 0
            env["answers"].append("••••0001")
            assert main(["connect", "--link"]) == 0
        engine = open_db()
        try:
            with engine.begin() as conn:
                led = Ledger(conn, 1)
                ambiguous = led.ambiguous_matches()
                surfaced = {i for c in led.possible_duplicates() for i in c}
                txns = led.transactions()
                assert len(led.accounts()) == 1
                # ambiguity is surfaced, never merged: no purchase is lost, and
                # each extra row sits in a surfaced Possible Duplicate
                assert {i for c in ambiguous for i in c} <= surfaced
                assert n <= len(txns) <= n + sum(len(c) for c in ambiguous) // 2
        finally:
            engine.dispose()
    if not ambiguous:
        assert len(txns) == n
        total = sum(t.amount for t in txns)
        assert total == Decimal(sum(t.amount_minor for t in h.txns)).scaleb(-2)
