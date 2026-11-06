from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from finance_agent.ledger import Ledger
from finance_agent.storage import open_db


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
