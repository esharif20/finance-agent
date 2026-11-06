"""Verifier (#25): adversarial checks on the SQLCipher gate and Keychain keys.
The Keychain is conftest's in-memory fake; the real one is never touched."""

import argparse
import re
import sqlite3
import traceback
from collections.abc import Iterator
from pathlib import Path

import keyring
import keyring.backend
import pytest
from alembic import command
from alembic.config import Config
from keyring.errors import KeyringError, PasswordDeleteError, PasswordSetError

from finance_agent import storage
from finance_agent.ledger import Ledger
from finance_agent.storage import (
    MissingKey,
    UnencryptedDatabase,
    keychain,
    keyed_ref,
    open_db,
)
from synthetic_lloyds import generate_histories, render_export

HEADER = b"SQLite format 3\x00"


@pytest.fixture
def real_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the default (real) DB location at a temp dir."""
    path = tmp_path / "Library/Application Support/FinanceAgent/ledger.db"
    monkeypatch.setattr(storage, "DEFAULT_DB", path)
    monkeypatch.delenv(storage.DB_ENV, raising=False)
    return path


def test_plaintext_file_at_real_location_is_refused_and_untouched(
    real_location: Path,
) -> None:
    real_location.parent.mkdir(parents=True)
    with sqlite3.connect(real_location) as c:
        c.execute("create table account (id integer primary key, external_ref text)")
        c.execute("insert into account values (1, 'planted')")
    before = real_location.read_bytes()

    with pytest.raises(UnencryptedDatabase):
        open_db()
    assert real_location.read_bytes() == before


def test_plaintext_sqlite_cannot_read_the_real_db(real_location: Path) -> None:
    open_db().dispose()
    with pytest.raises(sqlite3.DatabaseError):
        with sqlite3.connect(real_location) as c:
            c.execute("select * from sqlite_master").fetchall()


def test_encrypted_bytes_hold_no_header_and_no_synthetic_text(
    tmp_path: Path,
) -> None:
    h, _ = generate_histories(seed=7, days=20)
    export = tmp_path / "e.csv"
    export.write_text(render_export(h, 0, 10).text)
    path = tmp_path / "db" / "ledger.db"
    eng = open_db(path)
    with eng.begin() as conn:
        Ledger(conn, 1).import_files([export])
    eng.dispose()

    descriptions = {t.description for t in h.txns[:10]}
    assert descriptions
    for f in (tmp_path / "db").iterdir():
        data = f.read_bytes()
        assert HEADER not in data
        for d in descriptions:
            assert d.encode() not in data, "synthetic description in plaintext"


def test_missing_db_key_for_existing_db_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    open_db(path).dispose()
    keyring.delete_password(keychain.DB_SERVICE, keychain.ACCOUNT)
    before = path.read_bytes()

    with pytest.raises(MissingKey):
        open_db(path)
    assert keyring.get_password(keychain.DB_SERVICE, keychain.ACCOUNT) is None
    assert path.read_bytes() == before


def test_missing_hmac_key_for_existing_db_fails_closed(tmp_path: Path) -> None:
    open_db(tmp_path / "ledger.db").dispose()
    keyring.delete_password(keychain.HMAC_SERVICE, keychain.ACCOUNT)

    with pytest.raises(MissingKey):
        keyed_ref("00-00-00 00000000")
    assert keyring.get_password(keychain.HMAC_SERVICE, keychain.ACCOUNT) is None


class _Exploding(keyring.backend.KeyringBackend):
    priority = 1  # pyright: ignore[reportAssignmentType]

    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringError("backend unavailable")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise PasswordSetError("backend unavailable")

    def delete_password(self, service: str, username: str) -> None:
        raise PasswordDeleteError("backend unavailable")


@pytest.fixture
def exploding() -> Iterator[None]:
    prev = keyring.get_keyring()
    keyring.set_keyring(_Exploding())
    yield
    keyring.set_keyring(prev)


def test_keyring_failure_carries_no_key_material(
    tmp_path: Path, exploding: None
) -> None:
    with pytest.raises(KeyringError) as info:
        open_db(tmp_path / "ledger.db")
    err: BaseException = info.value
    text = "".join(traceback.format_exception(err))
    # No 64-hex-char run (a 32-byte key) anywhere in the error chain.
    assert not re.search(r"[0-9a-fA-F]{64}", text)
    assert not (tmp_path / "ledger.db").exists()


def test_alembic_cli_refuses_the_real_db_location(real_location: Path) -> None:
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(storage.__file__).parent / "migrations")
    )
    cfg.cmd_opts = argparse.Namespace(x=[f"db={real_location}"])
    with pytest.raises(SystemExit, match="scratch databases only"):
        command.upgrade(cfg, "head")
    assert not real_location.exists()
