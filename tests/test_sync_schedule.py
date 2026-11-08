"""#327: the scheduled sync inside the `fa` server, its settings, the profile
refresh after a sync, and `fa scan` / `fa status`. Fake syncs and clocks; no
network, and the profile paths never reach the Keychain."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import keyring
import pytest
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.api.scheduler import Scheduler
from finance_agent.cli import main
from finance_agent.engine import profile as pf
from finance_agent.ledger import Ledger
from finance_agent.storage import sync_schedule
from finance_agent.storage.sync_schedule import Schedule
from synthetic_lloyds import generate_histories, render_export

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
UID = 1


def _no_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("touched the Keychain")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, refuse)


# --- settings ---------------------------------------------------------------------


def test_settings_default_on_every_six_hours_and_survive_a_bad_file(
    tmp_path: Path,
) -> None:
    p = tmp_path / sync_schedule.FILENAME
    assert sync_schedule.load(p) == Schedule(True, 6)
    p.write_text("{not json")
    assert sync_schedule.load(p) == Schedule(True, 6)
    sync_schedule.save(Schedule(False, 12), p)
    assert sync_schedule.load(p) == Schedule(False, 12)
    p.write_text('{"enabled": true, "every_hours": 0}')
    assert sync_schedule.load(p) == Schedule(True, 6)
    with pytest.raises(ValueError, match="hours"):
        sync_schedule.save(Schedule(True, 0), p)


def test_next_is_one_interval_after_the_last_attempt() -> None:
    on = Schedule(True, 6)
    assert sync_schedule.next_at(on, None, NOW) == NOW
    assert sync_schedule.next_at(on, NOW - timedelta(hours=1), NOW) == NOW + timedelta(
        hours=5
    )
    assert (
        sync_schedule.next_at(on, NOW - timedelta(days=2), NOW) == NOW
    )  # overdue: now
    assert sync_schedule.next_at(Schedule(False, 6), None, NOW) is None


# --- the scheduler ----------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


def _scheduler(engine: Engine, sync: Any, s: Schedule, clock: Clock) -> Scheduler:
    with engine.begin() as conn:
        Ledger(conn, UID).ledger_version()  # the user row exists
    return Scheduler(engine, UID, sync, lambda: s, clock)


def test_it_syncs_when_due_and_waits_a_full_interval_after(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[datetime] = []
    clock = Clock()
    sched = _scheduler(
        engine, lambda: calls.append(clock.now), Schedule(True, 6), clock
    )
    _no_keychain(monkeypatch)
    assert sched.tick() and calls == [NOW]  # never synced: now
    clock.now += timedelta(hours=5, minutes=59)
    assert not sched.tick()
    clock.now += timedelta(minutes=1)
    assert sched.tick() and len(calls) == 2


def test_a_recent_sync_elsewhere_pushes_it_back(engine: Engine) -> None:
    calls: list[int] = []
    clock = Clock()
    sched = _scheduler(engine, lambda: calls.append(1), Schedule(True, 6), clock)
    with engine.begin() as conn:  # `fa sync` an hour ago
        Ledger(conn, UID).record_sync_success(NOW - timedelta(hours=1))
    assert not sched.tick() and calls == []
    clock.now += timedelta(hours=5)
    assert sched.tick() and calls == [1]


def test_off_means_never(engine: Engine) -> None:
    calls: list[int] = []
    sched = _scheduler(engine, lambda: calls.append(1), Schedule(False, 6), Clock())
    assert not sched.tick() and calls == []


def test_a_failing_sync_logs_its_class_only_and_is_not_retried_at_once(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    def boom() -> None:
        raise ValueError("https://user:secret@host/simplefin")

    clock = Clock()
    sched = _scheduler(engine, boom, Schedule(True, 6), clock)
    assert sched.tick()
    assert "ValueError" in caplog.text and "secret" not in caplog.text
    clock.now += timedelta(minutes=5)
    assert not sched.tick()


def test_the_loop_stops(engine: Engine) -> None:
    sched = _scheduler(engine, lambda: None, Schedule(False, 6), Clock())
    t = sched.start()
    sched.stop()
    t.join(timeout=5)
    assert not t.is_alive()


# --- the refresh hook in the sync path -------------------------------------------


def test_a_sync_refreshes_the_profile(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_sync(*_: object) -> list[object]:
        return []

    def silent(*_: str) -> None:
        return None

    monkeypatch.setattr(api, "run_sync", no_sync)
    monkeypatch.setattr(pf, "start", pf.refresh_quietly)  # inline: no thread race
    with engine.begin() as conn:
        _, before = pf.profile_context(conn, UID)
    _no_keychain(monkeypatch)
    api.sync_once(engine, UID, silent)
    with engine.begin() as conn:
        _, after = pf.profile_context(conn, UID)
    assert after is not before  # dropped and rebuilt, never the pre-sync one


# --- fa scan, fa status, fa sync --auto ---------------------------------------------


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    h, _ = generate_histories(seed=3, days=60)
    csv = tmp_path / "lloyds.csv"
    csv.write_text(render_export(h, 0, 120).text)
    assert main(["import", str(csv)]) == 0
    return tmp_path


def test_scan_prints_progress_then_the_summary(
    ledger: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    capsys.readouterr()
    assert main(["scan"]) == 0
    assert main(["scan"]) == 0  # idempotent
    out = capsys.readouterr().out
    assert out.startswith("Scanning your history") and "Profile ready" in out
    assert "tokens" in out and "£" not in out  # figures only with --show
    assert "£" not in caplog.text
    assert main(["scan", "--show"]) == 0 and "Accounts:" in capsys.readouterr().out


def test_status_shows_the_next_sync_and_the_toggle_turns_it_off(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "Next sync" in (out := capsys.readouterr().out) and "every 6h" in out
    assert main(["sync", "--every", "12"]) == 0
    assert "every 12h" in capsys.readouterr().out
    assert main(["sync", "--auto", "off"]) == 0
    assert sync_schedule.load() == Schedule(False, 12)
    capsys.readouterr()
    assert main(["status"]) == 0
    assert "off (`fa sync --auto on`)" in capsys.readouterr().out
    assert main(["sync", "--every", "0"]) == 1
