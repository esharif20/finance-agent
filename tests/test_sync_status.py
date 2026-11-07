"""#28: freshness, failure and consent status. In process, fake transport, in-memory
Keychain; no network, no real LaunchAgents."""

import logging
from datetime import datetime, timedelta
from typing import Any

import pytest

from finance_agent.cli import main, watch
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger, SyncStatus
from finance_agent.storage import open_db
from test_cli_sync import env  # noqa: F401  (shared fixture)

__all__ = ["env"]


def _status() -> SyncStatus:
    engine = open_db()
    with engine.begin() as conn:
        st = Ledger(conn, 1).sync_status()
    engine.dispose()
    return st


def test_a_failed_sync_shows_in_summary_with_class_and_count_only(
    env: dict[str, Any],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    assert main(["connect"]) == 0
    assert main(["sync"]) == 0
    env["t"].fail = OSError(f"boom {env['access']}")
    assert main(["sync"]) == 1
    assert main(["sync"]) == 1
    capsys.readouterr()
    assert main(["summary", "2026-01"]) == 0
    out = capsys.readouterr().out
    assert "Last sync " in out
    assert "last sync failed" in out and "SimpleFINError, 2 in a row" in out
    assert env["access"] not in out + caplog.text
    assert "sync_failed class=SimpleFINError" in caplog.text
    env["t"].fail = None
    assert main(["sync"]) == 0
    assert _status().failure_class is None
    assert _status().failures_in_a_row == 0


def test_consent_warns_inside_14_days_of_expiry(
    env: dict[str, Any],  # noqa: F811
) -> None:
    today = datetime.now(simplefin.LONDON).date()
    engine = open_db()
    with engine.begin() as conn:
        ledger = Ledger(conn, 1)
        assert ledger.sync_status(today).consent_expires_on is None
        ledger.record_consent_renewed(today - timedelta(days=75))
        st = ledger.sync_status(today)
        assert (st.consent_days_left, st.consent_warning) == (15, False)
        ledger.record_consent_renewed(today - timedelta(days=76))
        st = ledger.sync_status(today)
        assert (st.consent_days_left, st.consent_warning) == (14, True)
        ledger.record_consent_renewed(today - timedelta(days=95))
        assert ledger.sync_status(today).consent_days_left == -5
    engine.dispose()


def test_connect_renewed_resets_the_consent_clock(
    env: dict[str, Any],  # noqa: F811
) -> None:
    assert main(["connect", "--renewed"]) == 0
    assert env["t"].calls == []
    assert _status().consent_days_left == simplefin.CONSENT_DAYS


def test_scheduled_sync_alerts_once_per_state(
    env: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes: list[tuple[str, str]] = []

    def record(title: str, body: str) -> None:
        notes.append((title, body))

    monkeypatch.setattr(watch, "osascript", record)
    assert main(["connect"]) == 0
    assert main(["sync", "--notify"]) == 0
    assert notes == []
    env["t"].fail = OSError(f"x {env['access']}")
    for _ in range(3):
        assert main(["sync", "--notify"]) == 1
    assert len(notes) == 1  # not re-announced on every run
    assert "SimpleFINError" in notes[0][1] and env["access"] not in notes[0][1]
    env["t"].fail = None
    assert main(["sync", "--notify"]) == 0
    env["t"].fail = OSError("again")
    assert main(["sync", "--notify"]) == 1
    assert len(notes) == 2  # recovered, then failed again: new alert
