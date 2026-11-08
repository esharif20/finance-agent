"""The spend store (ADR 0016): `spend.sqlite` beside the ledger, in plain
SQLite because it holds no personal data, only buckets, model ids, token
counts, pence and times. It is not an Alembic database: the schema is created
on first use and versioned in `schema_version`.

Every reserve runs in `BEGIN IMMEDIATE`, so two processes cannot both take
the last pennies of a bucket. The gateway payload log lives here too: its
payloads hold placeholders only (`gateway.redact` builds them) and rows older
than 90 days are purged on every write."""

import json
import logging
import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from finance_agent.gateway import cost
from finance_agent.gateway.cloud import Usage

log = logging.getLogger(__name__)
FILENAME = "spend.sqlite"
VERSION = 1
KEEP = timedelta(days=90)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY,
    bucket TEXT NOT NULL,
    model TEXT NOT NULL,
    month TEXT NOT NULL,
    reserved_pence INTEGER NOT NULL CHECK (reserved_pence >= 0),
    cost_pence INTEGER CHECK (cost_pence >= 0),
    input_tokens INTEGER,
    output_tokens INTEGER,
    thinking_tokens INTEGER,
    model_version TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'settled', 'cancelled')),
    reserved_at TEXT NOT NULL,
    settled_at TEXT
);
CREATE INDEX IF NOT EXISTS spend_month ON spend (month, bucket);
CREATE TABLE IF NOT EXISTS gateway_payload (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    model TEXT NOT NULL,
    cost_pence INTEGER NOT NULL CHECK (cost_pence >= 0),
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS gateway_payload_conv
    ON gateway_payload (user_id, conversation_id);
"""


class NotOpen(ValueError):
    """The reservation is unknown, already settled or cancelled."""


@dataclass(frozen=True)
class Payload:
    conversation_id: str
    payload: dict[str, Any]
    model: str
    cost_pence: int
    sent_at: datetime


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC)


class SpendStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as db:
            db.executescript(_SCHEMA)  # idempotent; executescript commits itself
        with self._tx() as db:
            if db.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0:
                db.execute("INSERT INTO schema_version VALUES (?)", (VERSION,))

    @classmethod
    def beside(cls, ledger_path: Path) -> "SpendStore":
        return cls(ledger_path.with_name(FILENAME))

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection]:
        with closing(
            sqlite3.connect(self.path, isolation_level=None, timeout=10)
        ) as db:
            db.execute("BEGIN IMMEDIATE")  # take the write lock before reading
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    @staticmethod
    def _committed(db: sqlite3.Connection, month: str) -> tuple[dict[str, int], ...]:
        spent: dict[str, int] = {}
        held: dict[str, int] = {}
        rows = db.execute(
            "SELECT bucket, status, sum(coalesce(cost_pence, reserved_pence)) "
            "FROM spend WHERE month = ? AND status != 'cancelled' "
            "GROUP BY bucket, status",
            (month,),
        )
        for bucket, status, total in rows:
            (held if status == "open" else spent)[bucket] = total
        return spent, held

    def reserve(
        self,
        bucket: str,
        model: str,
        max_in: int,
        max_out: int,
        now: datetime | None = None,
    ) -> cost.Allowed | cost.Warn | cost.Denied:
        at = _now(now)
        with self._tx() as db:
            spent, held = self._committed(db, cost.month_of(at))
            committed = spent.get(bucket, 0) + held.get(bucket, 0)
            d = cost.reserve(
                bucket, model, max_in, max_out, committed=committed, now=at
            )
            if isinstance(d, cost.Denied):
                log.warning("spend: bucket %s capped at %d pence", bucket, committed)
                return d
            r = d.reservation
            cur = db.execute(
                "INSERT INTO spend (bucket, model, month, reserved_pence, status, "
                "reserved_at) VALUES (?, ?, ?, ?, 'open', ?)",
                (r.bucket, r.model, r.month, r.pence, at.isoformat()),
            )
            r = replace(r, id=cur.lastrowid or 0)
        if isinstance(d, cost.Warn):
            log.warning("spend: bucket %s past 80%% of its cap", bucket)
            return cost.Warn(r)
        return cost.Allowed(r)

    def settle(
        self, reservation: cost.Reservation, usage: Usage, now: datetime | None = None
    ) -> int:
        pence = cost.settle(reservation, usage)
        with self._tx() as db:
            cur = db.execute(
                "UPDATE spend SET status = 'settled', cost_pence = ?, "
                "input_tokens = ?, output_tokens = ?, thinking_tokens = ?, "
                "model_version = ?, settled_at = ? WHERE id = ? AND status = 'open'",
                (
                    pence,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.thinking_tokens,
                    usage.model_version[:64],
                    _now(now).isoformat(),
                    reservation.id,
                ),
            )
            if cur.rowcount != 1:
                raise NotOpen("no open reservation with this id")
        log.info("spend: settled %d pence in %s", pence, reservation.bucket)
        return pence

    def cancel(self, reservation: cost.Reservation) -> None:
        """A call that never reached the host (a local refusal) costs nothing."""
        with self._tx() as db:
            db.execute(
                "UPDATE spend SET status = 'cancelled' "
                "WHERE id = ? AND status = 'open'",
                (reservation.id,),
            )

    def month_status(self, now: datetime | None = None) -> list[cost.BucketStatus]:
        with self._tx() as db:
            spent, held = self._committed(db, cost.month_of(_now(now)))
        return cost.month_status(spent, held)

    # ---- the gateway payload log --------------------------------------------

    def log_payload(
        self,
        user_id: int,
        conversation_id: str,
        payload: dict[str, Any],
        model: str,
        cost_pence: int,
        now: datetime | None = None,
    ) -> None:
        at = _now(now)
        with self._tx() as db:
            db.execute(
                "DELETE FROM gateway_payload WHERE sent_at < ?",
                ((at - KEEP).isoformat(),),
            )
            db.execute(
                "INSERT INTO gateway_payload (user_id, conversation_id, payload, "
                "model, cost_pence, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    conversation_id,
                    json.dumps(payload, sort_keys=True),
                    model,
                    cost_pence,
                    at.isoformat(),
                ),
            )
        log.info("gateway: logged a payload, %d pence", cost_pence)

    def list_payloads(self, user_id: int, conversation_id: str) -> list[Payload]:
        with self._tx() as db:
            rows = db.execute(
                "SELECT conversation_id, payload, model, cost_pence, sent_at "
                "FROM gateway_payload WHERE user_id = ? AND conversation_id = ? "
                "ORDER BY sent_at, id",
                (user_id, conversation_id),
            ).fetchall()
        return [
            Payload(c, json.loads(p), m, n, datetime.fromisoformat(s))
            for c, p, m, n, s in rows
        ]
