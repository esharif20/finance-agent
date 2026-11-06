"""Storage: the single connection factory, migrations and the user-scoped
data-access layer. Nothing outside this module builds an engine or an
unscoped query."""

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    Connection,
    CursorResult,
    Delete,
    Engine,
    Executable,
    Select,
    Table,
    create_engine,
    delete,
    event,
    insert,
    select,
)

from finance_agent.storage import schema

DB_ENV = "FA_DB_PATH"
DEFAULT_DB = Path.home() / "Library/Application Support/FinanceAgent/ledger.db"
_MIGRATIONS = Path(__file__).parent / "migrations"


class UnsafeDatabasePath(ValueError):
    pass


def default_db_path() -> Path:
    return Path(os.environ.get(DB_ENV) or DEFAULT_DB)


def _inside_repo(path: Path) -> bool:
    return any((p / ".git").exists() for p in path.resolve().parents)


def open_db(path: Path | None = None) -> Engine:
    """The connection factory: validate the path, create it, migrate to head.

    Swap point for SQLCipher in M3 (ADR 0002).
    """
    path = path or default_db_path()
    if _inside_repo(path):
        raise UnsafeDatabasePath("refusing to open a database inside a repository")
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}")
    event.listen(engine, "connect", _sqlite_pragmas)
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    return engine


def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
    dbapi_conn.execute("PRAGMA foreign_keys = ON")


class Scoped:
    """The data-access layer: every read and write is scoped to one user."""

    def __init__(self, conn: Connection, user_id: int) -> None:
        self.conn = conn
        self.user_id = user_id
        u = schema.user
        if conn.execute(select(u.c.id).where(u.c.id == user_id)).first() is None:
            conn.execute(insert(u).values(id=user_id, created_at=datetime.now(UTC)))

    def select(self, table: Table, *cols: Any) -> Select[Any]:
        return (
            select(*(cols or (table,)))
            .select_from(table)
            .where(table.c.user_id == self.user_id)
        )

    def insert(self, table: Table, **values: Any) -> int:
        """Insert one row for this user; returns its first primary-key value."""
        res = self.conn.execute(insert(table).values(user_id=self.user_id, **values))
        key: tuple[Any, ...] = tuple(res.inserted_primary_key or ())
        return int(key[0])

    def delete(self, table: Table) -> Delete:
        return delete(table).where(table.c.user_id == self.user_id)

    def execute(self, stmt: Executable) -> CursorResult[Any]:
        return self.conn.execute(stmt)
