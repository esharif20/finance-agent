"""The scheduled sync's settings (#327): on or off, and every how many hours.

A small JSON file beside the database, like `spend.sqlite`: it holds no
financial data, so it needs no encryption and no migration. A missing or
unreadable file means the default, on every DEFAULT_HOURS hours.

The next sync is one interval after the last attempt (success or failure), so a
`fa sync` or a button press pushes the scheduled one back, and a failure is
not retried until the next interval.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

from finance_agent.storage import default_db_path

FILENAME = "sync-schedule.json"
DEFAULT_HOURS = 6  # the aggregator refreshes about 4 times a day (cli.schedule)
HOURS = range(1, 7 * 24 + 1)


@dataclass(frozen=True)
class Schedule:
    enabled: bool = True
    every_hours: int = DEFAULT_HOURS


def path() -> Path:
    return default_db_path().with_name(FILENAME)


def load(at: Path | None = None) -> Schedule:
    try:
        doc = json.loads((at or path()).read_text())
        s = Schedule(bool(doc["enabled"]), int(doc["every_hours"]))
    except (OSError, ValueError, KeyError, TypeError):
        return Schedule()
    return s if s.every_hours in HOURS else Schedule(s.enabled)


def save(s: Schedule, at: Path | None = None) -> None:
    if s.every_hours not in HOURS:
        raise ValueError(f"every must be {HOURS.start} to {HOURS.stop - 1} hours")
    p = at or path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(s)))


def next_at(s: Schedule, last: datetime | None, now: datetime) -> datetime | None:
    """When the next scheduled sync is due; None when off, `now` when overdue."""
    if not s.enabled:
        return None
    return now if last is None else max(now, last + timedelta(hours=s.every_hours))
