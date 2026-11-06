from collections.abc import Callable, Iterator
from pathlib import Path

import keyring
import keyring.backend
import pytest
from sqlalchemy import Engine

from finance_agent.ledger import Ledger
from finance_agent.storage import open_db


class MemoryKeyring(keyring.backend.KeyringBackend):
    """In-memory stand-in for the macOS Keychain; tests never touch the real one."""

    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.items.pop((service, username), None)


# Installed at import, before any test or fixture can reach keyring's default.
keyring.set_keyring(MemoryKeyring())


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A fresh, migrated temporary database per test."""
    eng = open_db(tmp_path / "db" / "ledger.db")
    yield eng
    eng.dispose()


@pytest.fixture
def ledger_for(engine: Engine) -> Iterator[Callable[[int], Ledger]]:
    """ledger_for(user_id) -> Ledger on the temp database, one shared transaction."""
    with engine.begin() as conn:
        yield lambda user_id: Ledger(conn, user_id)


@pytest.fixture
def write_export(tmp_path: Path) -> Callable[[str], Path]:
    n = 0

    def write(text: str) -> Path:
        nonlocal n
        n += 1
        p = tmp_path / f"export{n}.csv"
        p.write_text(text)
        return p

    return write
