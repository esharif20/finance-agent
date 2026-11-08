"""The briefing store (#309): `briefing.sqlite` beside the ledger, in plain
SQLite like `spend.sqlite`. It holds topic names, the user's tick or block on
each, and the news items fetched for them: public web text and URLs, never an
amount, an account or a Merchant's bank text. Not an Alembic database (the
ledger's migrations stay the ledger's): the schema is created on first use and
versioned in `schema_version`.

Items are deduped across days (the same topic and normalised headline, or the
same first source) and purged once older than `gateway.brief.STALE_DAYS`."""

import json
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from finance_agent.gateway.brief import STALE_DAYS, Item, Source

FILENAME = "briefing.sqlite"
VERSION = 1
State = Literal["on", "off"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS topic (
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL COLLATE NOCASE,
    state TEXT NOT NULL CHECK (state IN ('on', 'off')),
    PRIMARY KEY (user_id, name)
);
CREATE TABLE IF NOT EXISTS item (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    topic TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    change TEXT NOT NULL,
    on_date TEXT NOT NULL,
    sources TEXT NOT NULL,
    first_url TEXT NOT NULL,
    dedupe TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE (user_id, dedupe)
);
CREATE INDEX IF NOT EXISTS item_user ON item (user_id, on_date);
"""


@dataclass(frozen=True)
class Stored:
    id: int
    topic: str
    headline: str
    summary: str
    change: str
    on: date
    sources: tuple[Source, ...]
    fetched_at: datetime
    read: bool


class BriefingStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=10)) as db:
            db.executescript(_SCHEMA)
        with self._tx() as db:
            if db.execute("SELECT count(*) FROM schema_version").fetchone()[0] == 0:
                db.execute("INSERT INTO schema_version VALUES (?)", (VERSION,))

    @classmethod
    def beside(cls, ledger_path: Path) -> "BriefingStore":
        return cls(ledger_path.with_name(FILENAME))

    @staticmethod
    def exists_beside(ledger_path: Path) -> bool:
        return ledger_path.with_name(FILENAME).exists()

    @contextmanager
    def _tx(self) -> Generator[sqlite3.Connection]:
        with closing(
            sqlite3.connect(self.path, isolation_level=None, timeout=10)
        ) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    # ---- topics -------------------------------------------------------------

    def topics(self, user_id: int) -> dict[str, State]:
        with self._tx() as db:
            rows = db.execute(
                "SELECT name, state FROM topic WHERE user_id = ? ORDER BY name",
                (user_id,),
            ).fetchall()
        return dict(rows)

    def set_topic(self, user_id: int, name: str, state: State) -> None:
        with self._tx() as db:
            db.execute(
                "INSERT INTO topic (user_id, name, state) VALUES (?, ?, ?) "
                "ON CONFLICT (user_id, name) DO UPDATE SET state = excluded.state, "
                "name = excluded.name",
                (user_id, name, state),
            )

    def forget_topic(self, user_id: int, name: str) -> bool:
        with self._tx() as db:
            cur = db.execute(
                "DELETE FROM topic WHERE user_id = ? AND name = ?", (user_id, name)
            )
        return cur.rowcount > 0

    # ---- items --------------------------------------------------------------

    def add(
        self,
        user_id: int,
        items: Sequence[Item],
        today: date,
        now: datetime | None = None,
    ) -> int:
        """Store the new items; returns how many were new (not seen on an
        earlier day). Purges stale items first."""
        at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        cutoff = (today - timedelta(days=STALE_DAYS)).isoformat()
        added = 0
        with self._tx() as db:
            db.execute("DELETE FROM item WHERE on_date < ?", (cutoff,))
            for it in items:
                first = it.sources[0].url
                seen = db.execute(
                    "SELECT 1 FROM item WHERE user_id = ? AND (dedupe = ? "
                    "OR (first_url = ? AND topic = ? COLLATE NOCASE))",
                    (user_id, it.dedupe, first, it.topic),
                ).fetchone()
                if seen:
                    continue
                db.execute(
                    "INSERT INTO item (user_id, topic, headline, summary, change, "
                    "on_date, sources, first_url, dedupe, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        it.topic,
                        it.headline,
                        it.summary,
                        it.change,
                        it.on.isoformat(),
                        json.dumps([[s.url, s.title] for s in it.sources]),
                        first,
                        it.dedupe,
                        at,
                    ),
                )
                added += 1
        return added

    def items(self, user_id: int, today: date) -> list[Stored]:
        """Fresh items for topics not switched off, newest first."""
        cutoff = (today - timedelta(days=STALE_DAYS)).isoformat()
        with self._tx() as db:
            rows = db.execute(
                "SELECT i.id, i.topic, headline, summary, change, on_date, sources, "
                "fetched_at, read_at FROM item i LEFT JOIN topic t "
                "ON t.user_id = i.user_id AND t.name = i.topic "
                "WHERE i.user_id = ? AND on_date >= ? "
                "AND coalesce(t.state, 'on') = 'on' "
                "ORDER BY on_date DESC, i.id DESC",
                (user_id, cutoff),
            ).fetchall()
        return [
            Stored(
                id=r[0],
                topic=r[1],
                headline=r[2],
                summary=r[3],
                change=r[4],
                on=date.fromisoformat(r[5]),
                sources=tuple(Source(u, t) for u, t in json.loads(r[6])),
                fetched_at=datetime.fromisoformat(r[7]),
                read=r[8] is not None,
            )
            for r in rows
        ]

    def mark_read(
        self, user_id: int, item_id: int, now: datetime | None = None
    ) -> bool:
        at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        with self._tx() as db:
            cur = db.execute(
                "UPDATE item SET read_at = coalesce(read_at, ?) "
                "WHERE user_id = ? AND id = ?",
                (at, user_id, item_id),
            )
        return cur.rowcount > 0
