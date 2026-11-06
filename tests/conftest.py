from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from finance_agent import storage
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db

REAL_DATA_DIR = storage.DEFAULT_DB.parent


@pytest.fixture(autouse=True)
def _never_touch_real_data(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every test (and any subprocess it spawns) defaults to a temp database,
    and opening anything under the real data directory fails the test."""
    monkeypatch.setenv(
        storage.DB_ENV, str(tmp_path_factory.mktemp("data") / "ledger.db")
    )
    inside_repo = storage._inside_repo  # pyright: ignore[reportPrivateUsage]

    def guarded(path: Path) -> bool:
        if path.resolve().is_relative_to(REAL_DATA_DIR.resolve()):
            pytest.fail(f"test reached the real data directory: {REAL_DATA_DIR}")
        return inside_repo(path)

    monkeypatch.setattr(storage, "_inside_repo", guarded)


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
