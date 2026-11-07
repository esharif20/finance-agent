"""Storage: the single connection factory, migrations and the user-scoped
data-access layer. Nothing outside this module builds an engine or an
unscoped query."""

import hashlib
import hmac
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
    exc,
    insert,
    select,
    update,
)
from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BinaryExpression, ClauseElement, ColumnClause
from sqlalchemy.sql.selectable import CompoundSelect, SelectBase, Subquery
from sqlcipher3 import dbapi2 as sqlcipher  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

from finance_agent.storage import keychain, schema
from finance_agent.storage.keychain import MissingKey as MissingKey

DB_ENV = "FA_DB_PATH"
DEFAULT_DB = Path.home() / "Library/Application Support/FinanceAgent/ledger.db"
_MIGRATIONS = Path(__file__).parent / "migrations"


_PLAINTEXT_HEADER = b"SQLite format 3\x00"


class UnsafeDatabasePath(ValueError):
    pass


class UnencryptedDatabase(ValueError):
    pass


class WrongDatabaseKey(ValueError):
    pass


def is_encrypted(path: Path) -> bool:
    """True if `path` holds a non-empty database without SQLite's plaintext
    header. Commands that bring in real data (connect, sync) must check this."""
    try:
        with path.open("rb") as f:
            head = f.read(len(_PLAINTEXT_HEADER))
    except FileNotFoundError:
        return False
    return bool(head) and head != _PLAINTEXT_HEADER


def keyed_ref(ref: str) -> str:
    """HMAC-SHA256 of an ingest account ref under the Keychain-held key, so the
    stored value cannot be brute-forced from sort code and number alone."""
    key = keychain.secret(keychain.HMAC_SERVICE)
    return hmac.new(key, ref.encode(), hashlib.sha256).hexdigest()


class MissingDatabase(ValueError):
    """No ledger at the path, and the caller may not create one."""


class UnscopedQuery(RuntimeError):
    """A statement reached Scoped.execute without its user filter: a bug."""


def default_db_path() -> Path:
    return Path(os.environ.get(DB_ENV) or DEFAULT_DB)


def _inside_repo(path: Path) -> bool:
    return any((p / ".git").exists() for p in path.resolve().parents)


def open_db(path: Path | None = None, create: bool = True) -> Engine:
    """The connection factory: validate the path, open it with SQLCipher using
    the Keychain key (ADR 0002), migrate to head. Refuses plaintext databases.
    With create=False a missing ledger is refused, so serving the app never
    makes one (only `fa import` and `fa connect` do)."""
    path = path or default_db_path()
    if _inside_repo(path):
        raise UnsafeDatabasePath("refusing to open a database inside a repository")
    if not create and not path.is_file():
        raise MissingDatabase(f"no ledger at {path}; run `fa import <files>` first")
    fresh = not path.exists() or not path.stat().st_size
    if not fresh and not is_encrypted(path):
        raise UnencryptedDatabase("refusing to open an unencrypted database")
    # Keys are created only alongside a new database; a lost key never re-keys.
    key = keychain.secret(keychain.DB_SERVICE, create=fresh).hex()
    keychain.secret(keychain.HMAC_SERVICE, create=fresh)
    path.parent.mkdir(parents=True, exist_ok=True)

    def keyed_connect(dbapi_conn: Any, _record: Any) -> None:
        # Raw 256-bit key: no passphrase KDF. Must be the first statement.
        dbapi_conn.execute(f"PRAGMA key = \"x'{key}'\"")
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    # hide_parameters: an error message never quotes amounts or descriptions
    engine = create_engine(f"sqlite:///{path}", module=sqlcipher, hide_parameters=True)
    event.listen(engine, "connect", keyed_connect, insert=True)
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT count(*) FROM sqlite_master").scalar()
    except exc.DatabaseError:
        engine.dispose()
        raise WrongDatabaseKey("the Keychain key does not open this database") from None
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    return engine


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

    def update(self, table: Table) -> Update:
        """A user-scoped UPDATE; add `.where(...)` and `.values(...)`."""
        return update(table).where(table.c.user_id == self.user_id)

    def delete(self, table: Table) -> Delete:
        """A DELETE of this user's rows only; narrow it further with .where()."""
        return delete(table).where(table.c.user_id == self.user_id)

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

    def _scoped(self, stmt: Executable | SelectBase) -> bool:
        """Scoped at this level, and every nested SELECT (FROM, columns,
        WHERE, UPDATE values) scoped too."""
        return self._scoped_here(stmt) and all(
            self._scoped(s) for s in self._nested(stmt)
        )

    def _nested(self, stmt: Executable | SelectBase) -> list[SelectBase]:
        found: list[SelectBase] = []
        todo: list[ClauseElement] = list(stmt.get_children())  # type: ignore[attr-defined]
        while todo:
            x = todo.pop()
            if isinstance(x, SelectBase):
                found.append(x)
            else:
                todo += x.get_children()
        return found

    def _scoped_here(self, stmt: Executable | SelectBase) -> bool:
        if isinstance(stmt, CompoundSelect):
            return True  # its member SELECTs are checked as nested statements
        if isinstance(stmt, Select):
            roots: list[FromClause] = list(stmt.get_final_froms())
        elif isinstance(stmt, Update | Delete):
            # UPDATE/DELETE ... FROM: tables named only in WHERE are read too
            roots = list({id(x): x for x in [stmt.table, *self._tables(stmt)]}.values())
        else:
            return False
        tables: list[FromClause] = []
        subqueries = 0
        conds: list[ClauseElement | None] = [stmt.whereclause]
        while roots:
            f = roots.pop()
            if isinstance(f, Join):
                roots += [f.left, f.right]
                conds.append(f.onclause)
            elif isinstance(f, Subquery):
                subqueries += 1  # its SELECT is checked as a nested statement
            else:
                tables.append(f)
        filtered = {
            id(c.table) for e in conds if e is not None for c in self._filters(e)
        }
        return bool(tables or subqueries) and all(id(f) in filtered for f in tables)

    def _tables(self, stmt: Update | Delete) -> list[FromClause]:
        """Tables whose columns the WHERE names outside subqueries."""
        found: list[Any] = []
        todo: list[ClauseElement] = (
            [] if stmt.whereclause is None else [stmt.whereclause]
        )
        while todo:
            x = todo.pop()
            if isinstance(x, SelectBase):
                continue
            if isinstance(x, ColumnClause):
                owner: Any = x.table  # untyped in SQLAlchemy's stubs
                if isinstance(owner, Table):
                    found.append(owner)
                continue
            todo += x.get_children()
        return found

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
