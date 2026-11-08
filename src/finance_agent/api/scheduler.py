"""The scheduled sync inside the `fa` server process (#327): a daemon thread
that runs the same sync as `POST /api/sync`, through the same pinned host
(ADR 0013), every `sync_schedule` interval while the server runs.

It re-reads the settings each TICK, so turning it off or changing the interval
needs no restart. The last attempt is the Ledger's last success or failure, or
this thread's own last try (not connected, or another sync held the lock), so
nothing is retried before the interval is up. It logs exception classes only.
"""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine

from finance_agent.ledger import Ledger
from finance_agent.storage import sync_schedule

log = logging.getLogger(__name__)
TICK = 300  # seconds between looks at the settings and the clock


def last_attempt(engine: Engine, user_id: int) -> datetime | None:
    with engine.begin() as conn:
        st = Ledger(conn, user_id).sync_status()
    tried = [t for t in (st.last_sync_at, st.last_failure_at) if t is not None]
    return max(tried, default=None)


class Scheduler:
    def __init__(
        self,
        engine: Engine,
        user_id: int,
        sync: Callable[[], object],
        settings: Callable[[], sync_schedule.Schedule] = sync_schedule.load,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._engine, self._user, self._sync = engine, user_id, sync
        self._settings, self._clock = settings, clock
        self._tried: datetime | None = None
        self._stop = threading.Event()

    def tick(self) -> bool:
        """Sync once if it is due; True when it tried. Never raises."""
        now = self._clock()
        try:
            seen = [
                t for t in (last_attempt(self._engine, self._user), self._tried) if t
            ]
            due = sync_schedule.next_at(self._settings(), max(seen, default=None), now)
        except Exception as e:  # noqa: BLE001
            log.warning("sync schedule unreadable: %s", type(e).__name__)
            return False
        if due is None or due > now:
            return False
        self._tried = now
        try:
            self._sync()
        except Exception as e:  # noqa: BLE001 - the class is in sync status already
            log.warning("scheduled sync failed: %s", type(e).__name__)
        return True

    def run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(TICK)

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="sync-schedule", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()
