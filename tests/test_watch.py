import plistlib
import sqlite3
from pathlib import Path

import pytest

from finance_agent.cli import main, watch
from synthetic_lloyds import generate_histories, render_export


class Fakes:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []
        self.launchctl: list[list[str]] = []

    def notify(self, title: str, body: str) -> None:
        self.notes.append((title, body))

    def ctl(self, args: list[str]) -> None:
        self.launchctl.append(args)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Fakes]:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    folder = tmp_path / "drop"
    folder.mkdir()
    return folder, Fakes()


def test_new_file_imported_once_and_redrop_is_silent(
    env: tuple[Path, Fakes],
) -> None:
    folder, f = env
    h, _ = generate_histories(seed=3, days=30)
    (folder / "a.csv").write_text(render_export(h, 0, 40).text)
    assert watch.run(folder, f.notify) == 0
    assert f.notes == [("Finance Agent", "40 added, 0 already known, 0 rejected")]
    (folder / "copy.csv").write_text(render_export(h, 0, 40).text)
    assert watch.run(folder, f.notify) == 0
    assert len(f.notes) == 1  # nothing new, no notification


def test_malformed_file_notifies_line_only(env: tuple[Path, Fakes]) -> None:
    folder, f = env
    h, _ = generate_histories(seed=3, days=30)
    ex = render_export(h, 0, 40, malformed_after=10)
    (folder / "SECRETNAME.csv").write_text(ex.text)
    assert watch.run(folder, f.notify) == 1
    ((title, body),) = f.notes
    assert f"line {ex.malformed_line}" in body
    assert "SECRETNAME" not in title + body
    assert "£" not in body


def test_refuses_unencrypted_db(
    env: tuple[Path, Fakes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, f = env
    plain = tmp_path / "plain.db"
    con = sqlite3.connect(plain)
    con.execute("create table t(x)")
    con.commit()
    con.close()
    monkeypatch.setenv("FA_DB_PATH", str(plain))
    h, _ = generate_histories(seed=3, days=30)
    (folder / "a.csv").write_text(render_export(h, 0, 5).text)
    assert watch.run(folder, f.notify) == 1
    assert len(f.notes) == 1 and "refused" in f.notes[0][1].lower()


def test_install_writes_plist_and_loads_then_remove(tmp_path: Path) -> None:
    f = Fakes()
    agents = tmp_path / "LaunchAgents"
    folder = tmp_path / "drop"
    watch.install(folder, agents, f.ctl)
    plist = plistlib.loads((agents / f"{watch.LABEL}.plist").read_bytes())
    resolved = str(folder.resolve())
    assert plist["WatchPaths"] == [resolved]
    assert plist["ProgramArguments"][-3:] == ["watch", "run", resolved]
    assert folder.is_dir()
    assert f.launchctl[-1][0] == "load"
    watch.remove(agents, f.ctl)
    assert not (agents / f"{watch.LABEL}.plist").exists()
    assert f.launchctl[-1][0] == "unload"
    watch.remove(agents, f.ctl)  # idempotent


def test_cli_install_and_remove_use_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = Fakes()
    monkeypatch.setattr(watch, "_launchctl", f.ctl)
    monkeypatch.setattr(watch, "AGENTS_DIR", tmp_path / "agents")
    assert main(["watch", "install", str(tmp_path / "d")]) == 0
    assert (tmp_path / "agents" / f"{watch.LABEL}.plist").exists()
    assert main(["watch", "remove"]) == 0
    assert [c[0] for c in f.launchctl] == ["load", "unload"]
