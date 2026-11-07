"""The sync run shared by `fa sync` and `POST /api/sync` (#77): fetch from
SimpleFIN and apply, one at a time. The access URL is read from the Keychain here
and never printed, logged or put in an error."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine

from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger, SyncReport
from finance_agent.storage import default_db_path, keychain
from finance_agent.storage.lock import exclusive

log = logging.getLogger(__name__)
SERVICE = keychain.SIMPLEFIN_ACCESS_SERVICE
SYNC_LOCK = (
    ".sync.lock"  # in the data dir: scheduled, CLI and button runs never overlap
)


class NotConnected(ValueError):
    pass


def run_sync(
    engine: Engine,
    user_id: int,
    show: Callable[[SyncReport], None] | None = None,
    after: Callable[[], None] | None = None,
) -> list[SyncReport]:
    """Fetch and apply under the sync lock; raises `Busy` if another run holds it
    and `NotConnected` without an access URL. A failure is recorded by class name
    only, then re-raised. `after` runs once the outcome is recorded (the scheduled
    job's notifications)."""
    with exclusive(default_db_path().parent, SYNC_LOCK):
        access = keychain.get_text(SERVICE)
        if access is None:
            raise NotConnected("not connected; run `fa connect` first")
        try:
            reports = _fetch_and_apply(engine, user_id, access, show)
        except Exception as e:
            with engine.begin() as conn:
                Ledger(conn, user_id).record_sync_failure(
                    type(e).__name__, datetime.now(UTC)
                )
            log.warning("sync_failed class=%s", type(e).__name__)
            if after:
                after()
            raise
        with engine.begin() as conn:
            Ledger(conn, user_id).record_sync_success(datetime.now(UTC))
        if after:
            after()
        return reports


def _fetch_and_apply(
    engine: Engine,
    user_id: int,
    access: str,
    show: Callable[[SyncReport], None] | None,
) -> list[SyncReport]:
    today = datetime.now(simplefin.LONDON).date()
    with engine.begin() as conn:
        since = Ledger(conn, user_id).sync_since(today)
    reports: list[SyncReport] = []
    for start, end in simplefin.windows(since, today):
        raw = simplefin.fetch(access, start, end, simplefin.transport)
        with engine.begin() as conn:  # each response commits on its own
            r = Ledger(conn, user_id).apply_sync(raw)
        reports.append(r)
        if show:
            show(r)
    return reports
