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
    Connection,
    CursorResult,
    Delete,
    Engine,
    Executable,
    Select,
    Table,
    Update,
    create_engine,
    delete,
    event,
    exc,
    insert,
    select,
    update,
)
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


def default_db_path() -> Path:
    return Path(os.environ.get(DB_ENV) or DEFAULT_DB)


def _inside_repo(path: Path) -> bool:
    return any((p / ".git").exists() for p in path.resolve().parents)


def open_db(path: Path | None = None) -> Engine:
    """The connection factory: validate the path, open it with SQLCipher using
    the Keychain key (ADR 0002), migrate to head. Refuses plaintext databases."""
    path = path or default_db_path()
    if _inside_repo(path):
        raise UnsafeDatabasePath("refusing to open a database inside a repository")
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

    engine = create_engine(f"sqlite:///{path}", module=sqlcipher)
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

    def execute(self, stmt: Executable) -> CursorResult[Any]:
        return self.conn.execute(stmt)
