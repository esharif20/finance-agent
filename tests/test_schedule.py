"""#28: the daily sync agent. Temp LaunchAgents dir and a fake launchctl only."""

import plistlib
from pathlib import Path

import pytest

from finance_agent.cli import main, schedule, watch


def test_install_writes_a_daily_plist_and_remove_unloads_it(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    agents = tmp_path / "LaunchAgents"
    schedule.install(agents, calls.append)
    plist = plistlib.loads((agents / f"{schedule.LABEL}.plist").read_bytes())
    assert plist["ProgramArguments"][-2:] == ["sync", "--notify"]
    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert calls[-1][0] == "load"
    schedule.remove(agents, calls.append)
    assert not (agents / f"{schedule.LABEL}.plist").exists()
    assert calls[-1][0] == "unload"
    schedule.remove(agents, calls.append)  # idempotent


def test_plist_passes_the_database_path_through_only_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents = tmp_path / "a"
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "x.db"))
    schedule.install(agents, lambda _a: None)
    plist = plistlib.loads((agents / f"{schedule.LABEL}.plist").read_bytes())
    assert plist["EnvironmentVariables"] == {"FA_DB_PATH": str(tmp_path / "x.db")}
    monkeypatch.delenv("FA_DB_PATH")
    schedule.install(agents, lambda _a: None)
    plist = plistlib.loads((agents / f"{schedule.LABEL}.plist").read_bytes())
    assert "EnvironmentVariables" not in plist


def test_cli_install_and_uninstall_use_the_seams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(watch, "_launchctl", calls.append)
    monkeypatch.setattr(watch, "AGENTS_DIR", tmp_path / "agents")
    assert main(["sync", "--install"]) == 0
    assert (tmp_path / "agents" / f"{schedule.LABEL}.plist").exists()
    assert main(["sync", "--uninstall"]) == 0
    assert [c[0] for c in calls] == ["load", "unload"]
