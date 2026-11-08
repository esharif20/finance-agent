"""Anonymous demo visitors: a signed cookie names a visitor and a Persona, and
each visitor owns a copy of that Persona's ledger. Isolation is by file: every
visitor database is single-user (`current_user`), so a visitor can only ever
reach rows in the file their cookie's id names."""

import hmac
import os
import re
import secrets
import shutil
import threading
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.pool import NullPool

from finance_agent.demo.guard import inside

COOKIE = "fa_demo"
MAX_AGE = 14 * 24 * 3600
MAX_VISITORS = 200
_ID = re.compile(r"[0-9a-f]{32}")


@dataclass(frozen=True)
class Visitor:
    id: str  # 32 hex characters
    persona: int


def sign(secret: bytes, v: Visitor) -> str:
    body = f"{v.id}.{v.persona}"
    return f"{body}.{hmac.new(secret, body.encode(), sha256).hexdigest()}"


def verify(secret: bytes, cookie: str | None, personas: int) -> Visitor | None:
    """The visitor a cookie names, or None for anything forged or malformed."""
    parts = (cookie or "").split(".")
    if len(parts) != 3:
        return None
    vid, persona, mac = parts
    good = hmac.new(secret, f"{vid}.{persona}".encode(), sha256).hexdigest()
    if not hmac.compare_digest(mac.encode(), good.encode()):
        return None
    if not _ID.fullmatch(vid) or not persona.isdigit() or int(persona) >= personas:
        return None
    return Visitor(vid, int(persona))


def new_visitor(personas: int, avoid: int | None = None) -> Visitor:
    choices = [p for p in range(personas) if p != avoid] or [0]
    return Visitor(secrets.token_hex(16), secrets.choice(choices))


def plain_engine(path: Path, create: bool = False) -> Engine:
    """A plain-SQLite engine (synthetic data only: no SQLCipher, no Keychain).
    Without `create` the file must exist: a visitor evicted mid-request gets an
    error, never a fresh empty ledger left in their place."""
    mode = "rwc" if create else "rw"
    engine = create_engine(
        f"sqlite:///{path.resolve().as_uri()}?mode={mode}&uri=true",
        poolclass=NullPool,
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def _fk(dbapi_conn: Any, _record: Any) -> None:  # pyright: ignore[reportUnusedFunction]
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    return engine


class Visitors:
    """`FA_DEMO_DIR/personas/<n>.db` copied to `FA_DEMO_DIR/visitors/<id>.db`,
    at most MAX_VISITORS of them, the least recently used evicted first."""

    def __init__(self, root: Path, limit: int = MAX_VISITORS) -> None:
        self.root = root
        self.limit = limit
        self.personas = sorted(
            (root / "personas").glob("*.db"), key=lambda p: (len(p.stem), p.stem)
        )
        (root / "visitors").mkdir(exist_ok=True)
        # eviction unlinks under visitors/: it must not be a link elsewhere
        inside(root, root / "visitors")
        inside(root, root / "personas")
        self._lock = threading.Lock()

    def persona(self, n: int) -> Path:
        return inside(self.root, self.root / "personas" / f"{n}.db")

    def path(self, v: Visitor) -> Path:
        if not _ID.fullmatch(v.id):
            raise ValueError("bad visitor id")
        return inside(self.root, self.root / "visitors" / f"{v.id}.db")

    def open(self, v: Visitor) -> Path:
        """The visitor's ledger, created from their Persona if missing."""
        p = self.path(v)
        with self._lock:
            if p.is_file():
                os.utime(p)  # LRU: mtime is the last use
            else:
                self._copy(v, p)
                self._evict()
        return p

    def reset(self, v: Visitor) -> None:
        with self._lock:
            self._copy(v, self.path(v))
            self._evict()

    def drop(self, v: Visitor) -> None:
        with self._lock:
            self.path(v).unlink(missing_ok=True)

    def _copy(self, v: Visitor, p: Path) -> None:
        tmp = p.with_suffix(f".{secrets.token_hex(4)}.tmp")
        shutil.copyfile(self.persona(v.persona), tmp)
        os.replace(tmp, p)  # atomic: a reader sees the old file or the new one

    def _evict(self) -> None:
        files = sorted(
            (self.root / "visitors").glob("*.db"), key=lambda f: f.stat().st_mtime_ns
        )
        for f in files[: max(0, len(files) - self.limit)]:
            f.unlink(missing_ok=True)


_current: ContextVar[Engine | None] = ContextVar("demo_engine", default=None)


class VisitorEngine:
    """Stands in for `app.state.engine`: every attribute is the current
    request's visitor engine. Outside a visitor's request it raises, so there
    is no shared engine for any route to fall back on."""

    def __getattr__(self, name: str) -> Any:
        engine = _current.get()
        if engine is None:
            raise RuntimeError("no demo visitor for this request")
        return getattr(engine, name)


@contextmanager
def bound(path: Path) -> Generator[None]:
    engine = plain_engine(path)
    token = _current.set(engine)
    try:
        yield
    finally:
        _current.reset(token)
        engine.dispose()
