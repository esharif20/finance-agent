"""ADR 0002 gate: the database is SQLCipher-encrypted with a Keychain-held key,
and account refs are keyed HMACs. The Keychain here is conftest's in-memory fake."""

import hashlib
import hmac
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import keyring
import pytest
from sqlalchemy import select

from finance_agent import storage
from finance_agent.ingest.lloyds import account_ref
from finance_agent.ledger import Ledger
from finance_agent.storage import (
    UnencryptedDatabase,
    WrongDatabaseKey,
    is_encrypted,
    keychain,
    open_db,
    schema,
)
from synthetic_lloyds import generate_histories, render_export


def test_tests_never_touch_the_real_keychain() -> None:
    assert type(keyring.get_keyring()).__name__ == "MemoryKeyring"


def test_new_database_is_encrypted_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    open_db(path).dispose()

    assert not path.read_bytes().startswith(b"SQLite format 3\x00")
    assert is_encrypted(path)
    open_db(path).dispose()  # same Keychain key opens it again


def test_refuses_an_unencrypted_database(tmp_path: Path) -> None:
    path = tmp_path / "plain.db"
    with sqlite3.connect(path) as c:
        c.execute("create table t (a)")

    assert not is_encrypted(path)
    with pytest.raises(UnencryptedDatabase):
        open_db(path)


def test_missing_database_is_not_encrypted(tmp_path: Path) -> None:
    assert not is_encrypted(tmp_path / "nothing.db")


def test_cli_import_refuses_an_unencrypted_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance_agent.cli import main

    path = tmp_path / "plain.db"
    with sqlite3.connect(path) as c:
        c.execute("create table t (a)")
    monkeypatch.setenv(storage.DB_ENV, str(path))
    export = tmp_path / "e.csv"
    export.write_text(render_export(generate_histories(seed=1)[0], 0, 5).text)

    before = path.read_bytes()
    assert main(["import", str(export)]) == 1
    assert path.read_bytes() == before


def test_a_different_key_cannot_open_the_database(tmp_path: Path) -> None:
    path = tmp_path / "ledger.db"
    open_db(path).dispose()
    keyring.delete_password(keychain.DB_SERVICE, keychain.ACCOUNT)

    with pytest.raises(WrongDatabaseKey):
        open_db(path)


def test_key_never_reaches_disk_env_or_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    keyring.delete_password(keychain.DB_SERVICE, keychain.ACCOUNT)
    eng = open_db(tmp_path / "db" / "ledger.db")
    with eng.begin() as conn:
        Ledger(conn, 1).summarise_month("2026-01")
    eng.dispose()
    key = keychain.secret(keychain.DB_SERVICE)
    hmac_key = keychain.secret(keychain.HMAC_SERVICE)

    for secret in (key, hmac_key):
        needles = (secret, secret.hex().encode(), secret.hex().upper().encode())
        for f in (tmp_path / "db").iterdir():
            data = f.read_bytes()
            assert not any(n in data for n in needles)
        assert not any(secret.hex() in v.lower() for v in os.environ.values())
        assert secret.hex() not in caplog.text.lower()
        assert secret.hex() not in repr(eng.url).lower()


def test_account_ref_is_an_hmac_not_the_unkeyed_digest(
    ledger_for: Callable[[int], Ledger], tmp_path: Path
) -> None:
    h, _ = generate_histories(seed=3, days=10)
    export = tmp_path / "e.csv"
    export.write_text(render_export(h, 0, 5).text)
    ledger = ledger_for(1)
    ledger.import_files([export])

    stored = ledger._db.execute(  # pyright: ignore[reportPrivateUsage]
        select(schema.account.c.external_ref)
    ).scalar_one()
    raw = account_ref(h.sort_code, h.account_number)
    key = keychain.secret(keychain.HMAC_SERVICE)
    assert stored != raw
    assert stored == hmac.new(key, raw.encode(), hashlib.sha256).hexdigest()


def test_secret_is_generated_once_and_reused() -> None:
    keyring.delete_password(keychain.HMAC_SERVICE, keychain.ACCOUNT)
    first = keychain.secret(keychain.HMAC_SERVICE)
    assert len(first) == 32
    assert keychain.secret(keychain.HMAC_SERVICE) == first
