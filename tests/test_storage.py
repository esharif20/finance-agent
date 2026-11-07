from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from finance_agent import storage
from finance_agent.storage import UnsafeDatabasePath, open_db, schema

REPO = Path(__file__).resolve().parents[1]


def test_refuses_a_database_inside_the_repository() -> None:
    with pytest.raises(UnsafeDatabasePath):
        open_db(REPO / "ledger.db")
    assert not (REPO / "ledger.db").exists()


def test_default_location_is_application_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(storage.DB_ENV, raising=False)
    assert storage.default_db_path() == (
        Path.home() / "Library/Application Support/FinanceAgent/ledger.db"
    )


def test_migrations_produce_exactly_the_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), schema.metadata)
    assert diff == []


def test_every_table_has_user_id() -> None:
    for name, table in schema.metadata.tables.items():
        assert name == "user" or "user_id" in table.c, name


def test_schema_compiles_for_postgres() -> None:
    for table in schema.metadata.sorted_tables:
        CreateTable(table).compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]


def test_migrations_upgrade_from_0003_to_head(tmp_path: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    path = tmp_path / "db" / "ledger.db"
    path.parent.mkdir()
    eng = create_engine(f"sqlite:///{path}")
    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0003")
        assert "merchant" not in inspect(conn).get_table_names()
    eng.dispose()
    eng = open_db(path)  # upgrades the rest of the way
    with eng.connect() as conn:
        assert {"merchant", "categorisation"} <= set(inspect(conn).get_table_names())
        assert compare_metadata(MigrationContext.configure(conn), schema.metadata) == []
    eng.dispose()


def test_tests_can_never_open_the_real_data_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(storage.DB_ENV, raising=False)
    with pytest.raises(pytest.fail.Exception, match="real data directory"):
        open_db()
    with pytest.raises(pytest.fail.Exception, match="real data directory"):
        open_db(storage.DEFAULT_DB.parent / "other.db")


def test_tests_default_to_a_temporary_database() -> None:
    assert not storage.default_db_path().is_relative_to(storage.DEFAULT_DB.parent)


def test_open_without_create_refuses_a_missing_ledger(tmp_path: Path) -> None:
    db = tmp_path / "nowhere" / "ledger.db"
    with pytest.raises(storage.MissingDatabase):
        open_db(db, create=False)
    assert not db.parent.exists()
    open_db(db).dispose()
    open_db(db, create=False).dispose()  # an existing one opens
