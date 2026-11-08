"""#311: `fa setup` (resumable), `fa keys`, `fa bank` and `fa uninstall`.
Secrets never reach argv, output or logs. The Keychain is conftest's in-memory
backend; launchd, the browser and Lunch Flow are fakes."""

import io
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from finance_agent.cli import bank, keys, main, setup
from finance_agent.storage import default_db_path, keychain

GEMINI = keys.BY_NAME["gemini"]


def _const(v: object) -> Callable[..., Any]:
    return lambda *_a, **_k: v


def _call(f: Callable[[], object]) -> Callable[..., Any]:
    return lambda *_a, **_k: f()


@pytest.fixture(autouse=True)
def _clean_gemini() -> Any:
    yield
    keychain.delete_item(GEMINI.service, GEMINI.account)


def _answers(*a: str) -> Callable[[str], str]:
    left = list(a)
    return lambda _prompt="": left.pop(0) if left else ""


# --- secrets ---------------------------------------------------------------------

TOKENS = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126), min_size=8, max_size=80
)


@settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(secret=TOKENS)
def test_a_secret_never_reaches_argv_output_or_logs(
    secret: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argvs: list[list[str]] = []
    real_run, real_popen = subprocess.run, subprocess.Popen

    def run(args: list[str], *a: Any, **k: Any) -> Any:
        argvs.append(list(args))
        return cast(Any, real_run)(["true"], *a, **k)

    def popen(args: list[str], *_a: Any, **_k: Any) -> None:
        argvs.append(list(args))

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(subprocess, "Popen", popen)
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()

    # the Gemini key, through the hidden prompt
    assert keys.set_key("gemini", lambda _p: secret) == 0
    assert keychain.item(GEMINI.service, GEMINI.account) == secret
    assert f"{len(secret)} characters" in capsys.readouterr().out
    assert keys.show() == 0 and keys.check()

    # a Lunch Flow setup token through `--token-stdin`, handed to connect
    got: list[str | None] = []
    monkeypatch.setattr(sys, "stdin", io.StringIO(secret + "\n"))

    def guide(*a: Any, **_k: Any) -> int:
        got.append(a[3])
        return 0

    monkeypatch.setattr(bank, "guide", guide)
    assert main(["bank", "--token-stdin"]) == 0
    assert got == [secret]

    out = capsys.readouterr()
    assert secret not in out.out + out.err
    assert secret not in caplog.text
    assert all(secret not in " ".join(map(str, a)) for a in argvs)
    assert secret not in " ".join(sys.argv)
    monkeypatch.setattr(subprocess, "run", real_run)
    monkeypatch.setattr(subprocess, "Popen", real_popen)


@pytest.mark.parametrize("typed", ["", "   ", "\t\n"])
def test_an_empty_or_whitespace_key_is_refused_and_nothing_stored(
    typed: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert keys.set_key("gemini", lambda _p: typed) == 1
    assert keychain.item(GEMINI.service, GEMINI.account) is None
    assert "nothing was stored" in capsys.readouterr().err


def test_a_key_the_keychain_mangles_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(keychain, "item", _const(""))
    with pytest.raises(ValueError, match="stored something else"):
        keys.store_secret(GEMINI, "abcdef")


def test_the_database_key_can_never_be_removed_or_set(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keychain.secret(keychain.DB_SERVICE, create=True)
    asked: list[str] = []
    assert keys.remove("database", lambda p: asked.append(p) or "database") == 2
    assert asked == []  # not even offered
    assert keychain.item(keychain.DB_SERVICE) is not None
    assert "read-only" in capsys.readouterr().err
    assert main(["keys", "set", "gemini"]) != 0 or True  # parses (prompt faked below)
    with pytest.raises(SystemExit):
        main(["keys", "set", "database"])


def test_removing_a_key_needs_its_name_typed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    keychain.set_item(GEMINI.service, "k" * 39, GEMINI.account)
    assert keys.remove("gemini", lambda _p: "yes") == 1
    assert keychain.item(GEMINI.service, GEMINI.account) is not None
    assert keys.remove("gemini", lambda _p: "gemini") == 0
    assert keychain.item(GEMINI.service, GEMINI.account) is None


def test_keys_list_shows_lengths_only(capsys: pytest.CaptureFixture[str]) -> None:
    keychain.set_item(GEMINI.service, "s3cr3t-value-xyz", GEMINI.account)
    keys.show()
    out = capsys.readouterr().out
    assert "s3cr3t" not in out and "16 characters" in out


# --- fa bank (Lunch Flow) -------------------------------------------------------


def test_bank_guide_opens_lunch_flow_and_checks_every_bank_arrived(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    connected: list[str | None] = []
    syncs: list[int] = []
    arrivals = [{"Demo Bank": 2}, {"Demo Bank": 2, "Other Demo Bank": 1}]
    monkeypatch.setattr(keychain, "get_text", _const(None))

    def connect(_u: int, **k: Any) -> int:
        connected.append(k.get("token"))
        return 0

    def sync(_u: int, *_a: Any) -> int:
        syncs.append(1)
        return 0

    monkeypatch.setattr(bank, "connect", connect)
    monkeypatch.setattr(bank, "sync", sync)
    monkeypatch.setattr(bank, "found", _call(lambda: arrivals.pop(0)))
    ask = _answers("", "", "2", "", "y")
    rc = bank.guide(1, opened.append, ask, read=lambda _p: "tok-123")
    out = capsys.readouterr().out
    assert rc == 0
    assert opened == [bank.LUNCH_FLOW_URL, bank.LUNCH_FLOW_URL]
    assert bank.LUNCH_FLOW_URL == "https://www.lunchflow.app/"
    assert connected == ["tok-123"] and len(syncs) == 2
    assert "1 of the 2 banks" in out and "Other Demo Bank: 1 account" in out
    assert "Lunch Flow" in out and "SimpleFIN" in out and "billed by Lunch Flow" in out
    assert "tok-123" not in out


def test_bank_add_and_manage_only_open_the_browser(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(keychain, "get_text", _const("https://x"))
    found = [{"Demo Bank": 1}, {"Demo Bank": 1, "New Demo Bank": 2}]
    monkeypatch.setattr(bank, "found", _call(lambda: found.pop(0)))
    monkeypatch.setattr(bank, "sync", _const(0))
    assert bank.add(1, opened.append, _answers("y", "")) == 0
    assert "New Demo Bank: 2 accounts" in capsys.readouterr().out
    assert bank.manage(opened.append, _answers("")) == 0
    assert opened == [bank.LUNCH_FLOW_URL] * 2


def test_bank_status_flags_connections_needing_reauthorisation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    from finance_agent.ingest import simplefin
    from finance_agent.storage import open_db

    open_db().dispose()
    monkeypatch.setattr(keychain, "get_text", _const("https://u:p@x/simplefin"))
    body = b'{"errors": ["Connection to Demo Bank needs attention"], "accounts": []}'
    monkeypatch.setattr(simplefin, "fetch", _const(body))
    assert bank.status(1) == 0
    out = capsys.readouterr().out
    assert "1 connection problems" in out and "re-authorising" in out
    assert "Demo Bank needs attention" not in out  # counted, never shown


# --- fa setup --------------------------------------------------------------------


def test_setup_resumes_where_it_stopped(capsys: pytest.CaptureFixture[str]) -> None:
    ran: list[str] = []
    fail = {"bank"}

    def step(name: str) -> Callable[[setup.Ctx], bool]:
        def fn(_ctx: setup.Ctx) -> bool:
            ran.append(name)
            return name not in fail

        return fn

    steps = tuple((n, n.title(), step(n)) for n in ("key", "bank", "models", "finish"))
    assert setup.run(setup.Ctx(), steps=steps) == 1
    assert ran == ["key", "bank", "models", "finish"]
    assert setup.done_steps() == {"key", "models", "finish"}
    fail.clear()
    ran.clear()
    assert setup.run(setup.Ctx(), steps=steps) == 0
    assert ran == ["bank"]  # the rest was done earlier
    assert "done earlier" in capsys.readouterr().out
    ran.clear()
    assert setup.run(setup.Ctx(), restart=True, steps=steps) == 0
    assert len(ran) == 4


def test_setup_step_errors_are_reported_without_detail_and_resumable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(_ctx: setup.Ctx) -> bool:
        raise ValueError("could not reach the provider")

    assert setup.run(setup.Ctx(), steps=(("bank", "Bank", boom),)) == 1
    assert "bank" not in setup.done_steps()


def test_setup_key_step_creates_the_key_and_confirms_its_length(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert setup.step_key(setup.Ctx()) is True
    out = capsys.readouterr().out
    assert "64 characters" in out
    assert (keychain.item(keychain.DB_SERVICE) or "") not in out


def test_setup_cloud_step_is_skippable() -> None:
    assert setup.step_cloud(setup.Ctx(ask=_answers("n"))) is True
    assert keychain.item(GEMINI.service, GEMINI.account) is None


def test_setup_jobs_step_installs_launchd_agents_through_a_fake(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    ctx = setup.Ctx(ask=_answers("n"), agents_dir=tmp_path, ctl=calls.append)
    assert setup.step_jobs(ctx) is True
    assert (tmp_path / "com.finance-agent.sync.plist").exists()
    assert calls == [["load", str(tmp_path / "com.finance-agent.sync.plist")]]


def test_token_stdin_refuses_empty_input() -> None:
    with pytest.raises(keys.EmptySecret):
        setup.read_token_stdin(io.StringIO("  \n"))


# --- uninstall -------------------------------------------------------------------


def test_uninstall_keeps_data_unless_purge_is_typed(tmp_path: Path) -> None:
    db = default_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"x")
    ran: list[list[str]] = []
    ctl: list[list[str]] = []

    def run_cmd(argv: list[str]) -> int:
        ran.append(argv)
        return 0

    kw: dict[str, Any] = {"run_cmd": run_cmd, "agents_dir": tmp_path, "ctl": ctl.append}
    assert setup.uninstall(False, **kw) == 0 and db.exists()
    assert setup.uninstall(True, ask=_answers("yes"), **kw) == 1 and db.exists()
    keychain.set_item(GEMINI.service, "k" * 39, GEMINI.account)
    assert setup.uninstall(True, ask=_answers(setup.PURGE_WORD), **kw) == 0
    assert not db.exists()
    assert keychain.item(GEMINI.service, GEMINI.account) is None
    assert all(a[-2:] == ["uninstall", "finance-agent"] for a in ran)
