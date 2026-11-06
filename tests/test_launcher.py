"""`fa` / `fa stop`: no real server, browser or process is ever started here."""

import webbrowser
from pathlib import Path

import pytest

from finance_agent.cli import launcher
from finance_agent.cli import main as fa


class Fake:
    """Stands in for the process table, the HTTP probe and the browser."""

    def __init__(self, up: bool = False) -> None:
        self.up = up
        self.spawned = 0
        self.opened: list[str] = []
        self.killed: list[int] = []

    def probe(self, port: int) -> bool:
        return self.up

    def spawn(self, port: int) -> int:
        self.spawned += 1
        self.up = True
        return 4242

    def kill(self, pid: int) -> None:
        self.killed.append(pid)
        self.up = False

    def open(self, url: str) -> None:
        self.opened.append(url)


@pytest.fixture
def env(tmp_path: Path) -> tuple[Fake, launcher.Launcher]:
    f = Fake()
    return f, launcher.Launcher(
        tmp_path / "server.pid", 8765, f.probe, f.spawn, f.kill, f.open
    )


def test_fa_starts_the_server_writes_a_pid_file_and_opens_the_browser(
    env: tuple[Fake, launcher.Launcher], tmp_path: Path
) -> None:
    f, ln = env

    ln.start()

    assert f.spawned == 1
    assert (tmp_path / "server.pid").read_text().split() == ["4242", "8765"]
    assert f.opened == ["http://127.0.0.1:8765/"]


def test_fa_does_not_start_a_second_server(
    env: tuple[Fake, launcher.Launcher],
) -> None:
    f, ln = env
    ln.start()
    ln.start()

    assert f.spawned == 1
    assert len(f.opened) == 2


def test_fa_stop_kills_the_recorded_server_and_removes_the_pid_file(
    env: tuple[Fake, launcher.Launcher], tmp_path: Path
) -> None:
    f, ln = env
    ln.start()

    assert ln.stop() is True

    assert f.killed == [4242]
    assert not (tmp_path / "server.pid").exists()


def test_stop_never_kills_a_pid_when_our_server_is_not_answering(
    env: tuple[Fake, launcher.Launcher], tmp_path: Path
) -> None:
    f, ln = env
    (tmp_path / "server.pid").write_text("999 8765")  # stale: pid may be reused

    assert ln.stop() is False

    assert f.killed == []
    assert not (tmp_path / "server.pid").exists()


def test_start_fails_cleanly_when_the_server_never_answers(
    tmp_path: Path,
) -> None:
    f = Fake()
    f.spawn = lambda port: 1  # type: ignore[method-assign]  # never comes up
    ln = launcher.Launcher(
        tmp_path / "server.pid", 8765, f.probe, f.spawn, f.kill, f.open, wait=0
    )

    with pytest.raises(launcher.ServerDidNotStart):
        ln.start()

    assert f.opened == []


def test_fa_with_no_arguments_and_fa_stop_use_the_launcher(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    class Spy:
        def start(self) -> None:
            calls.append("start")

        def stop(self) -> bool:
            calls.append("stop")
            return False

    monkeypatch.setattr(launcher, "default", lambda: Spy())

    assert fa([]) == 0
    assert fa(["stop"]) == 0

    assert calls == ["start", "stop"]
    assert "not running" in capsys.readouterr().out


def test_fa_no_browser_env_keeps_the_browser_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("FA_NO_BROWSER", "1")

    def fail(url: str) -> bool:
        pytest.fail("opened " + url)

    monkeypatch.setattr(webbrowser, "open", fail)

    launcher.default().open_url("http://127.0.0.1:1/")
