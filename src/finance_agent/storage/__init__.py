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
    BindParameter,
    ColumnElement,
    Connection,
    CursorResult,
    Delete,
    Engine,
    Executable,
    FromClause,
    Join,
    Select,
    Table,
    Update,
    and_,
    create_engine,
    delete,
    event,
    insert,
    select,
    update,
)
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, ClauseElement, ColumnClause
from sqlalchemy.sql.selectable import SelectBase

from finance_agent.storage import schema

DB_ENV = "FA_DB_PATH"
DEFAULT_DB = Path.home() / "Library/Application Support/FinanceAgent/ledger.db"
_MIGRATIONS = Path(__file__).parent / "migrations"


class UnsafeDatabasePath(ValueError):
    pass


class UnscopedQuery(RuntimeError):
    """A statement reached Scoped.execute without its user filter: a bug."""


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
        """A DELETE of this user's rows only; narrow it further with .where()."""
        return delete(table).where(table.c.user_id == self.user_id)

    def update(self, table: Table) -> Update:
        """An UPDATE of this user's rows only; narrow it further with .where()."""
        return update(table).where(table.c.user_id == self.user_id)

    def ledger_version(self) -> int:
        u = schema.user
        stmt = select(u.c.ledger_version).where(u.c.id == self.user_id)
        return int(self.conn.execute(stmt).scalar_one())

    def bump_ledger_version(self) -> None:
        u = schema.user
        self.conn.execute(
            update(u)
            .where(u.c.id == self.user_id)
            .values(ledger_version=u.c.ledger_version + 1)
        )

    def on(self, table: FromClause, *cond: ColumnElement[bool]) -> ColumnElement[bool]:
        """A join condition that also keeps the joined table to this user; the
        foreign keys are single-column, so they alone would not."""
        return and_(*cond, table.c.user_id == self.user_id)

    def execute(self, stmt: Executable) -> CursorResult[Any]:
        """Run a SELECT, UPDATE or DELETE in which every table read is filtered
        to this user (WHERE, or ON for a joined table); refuse anything else."""
        if not self._scoped(stmt):
            raise UnscopedQuery("statement is not scoped to the user")
        return self.conn.execute(stmt)

    def _scoped(self, stmt: Executable) -> bool:
        if isinstance(stmt, Select):
            roots: list[FromClause] = list(stmt.get_final_froms())
        elif isinstance(stmt, Update | Delete):
            roots = [stmt.table]
        else:
            return False
        tables: list[FromClause] = []
        conds: list[ClauseElement | None] = [stmt.whereclause]
        while roots:
            f = roots.pop()
            if isinstance(f, Join):
                roots += [f.left, f.right]
                conds.append(f.onclause)
            else:
                tables.append(f)
        filtered = {
            id(c.table) for e in conds if e is not None for c in self._filters(e)
        }
        return bool(tables) and all(id(f) in filtered for f in tables)

    def _filters(self, e: ClauseElement) -> list[ColumnClause[Any]]:
        """Columns compared `user_id == this user` in `e`, not inside subqueries
        (a scoped subquery says nothing about the outer table)."""
        found: list[ColumnClause[Any]] = []
        todo = [e]
        while todo:
            x = todo.pop()
            if isinstance(x, SelectBase):
                continue
            if (
                isinstance(x, BinaryExpression)
                and x.operator is operators.eq
                and isinstance(x.left, ColumnClause)
                and x.left.name == "user_id"
                and isinstance(x.right, BindParameter)
                and x.right.value == self.user_id
            ):
                found.append(x.left)
            todo += x.get_children()
        return found
