"""The gateway payload log (#295): read per user and conversation, purged at
90 days, and the payload text never reaches a log line."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from finance_agent.storage.spend import SpendStore

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
P: dict[str, object] = {
    "intent": "spend_month",
    "question": "How much on {C1} in {D1}?",
    "facts": [],
}


def test_payloads_are_per_user_and_conversation(tmp_path: Path) -> None:
    s = SpendStore(tmp_path / "spend.sqlite")
    s.log_payload(1, "c1", P, "gemini-flash-latest", 2, now=NOW)
    s.log_payload(2, "c1", {**P, "intent": "other"}, "gemini-flash-latest", 3, now=NOW)
    s.log_payload(1, "c2", P, "gemini-flash-latest", 4, now=NOW)
    [mine] = s.list_payloads(1, "c1")
    assert (mine.payload, mine.cost_pence, mine.sent_at) == (P, 2, NOW)
    assert [p.payload["intent"] for p in s.list_payloads(2, "c1")] == ["other"]
    assert s.list_payloads(2, "c2") == []


def test_rows_older_than_90_days_are_purged_on_write(tmp_path: Path) -> None:
    s = SpendStore(tmp_path / "spend.sqlite")
    s.log_payload(1, "c", P, "m", 1, now=NOW - timedelta(days=90, seconds=1))
    s.log_payload(1, "c", P, "m", 1, now=NOW - timedelta(days=89))
    assert len(s.list_payloads(1, "c")) == 2  # reading never purges
    s.log_payload(1, "c", P, "m", 1, now=NOW)
    assert [p.sent_at for p in s.list_payloads(1, "c")] == [
        NOW - timedelta(days=89),
        NOW,
    ]


def test_logs_carry_pence_never_payload_text(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    s = SpendStore(tmp_path / "spend.sqlite")
    s.log_payload(1, "c", P, "m", 7, now=NOW)
    assert "7 pence" in caplog.text and "{C1}" not in caplog.text
