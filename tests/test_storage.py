from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from sqlcipher3 import dbapi2 as sqlcipher  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

from finance_agent import storage
from finance_agent.storage import UnsafeDatabasePath, keychain, open_db, schema

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
    from sqlalchemy import create_engine, event, inspect

    path = tmp_path / "db" / "ledger.db"
    path.parent.mkdir()
    key = keychain.secret(keychain.DB_SERVICE, create=True).hex()
    keychain.secret(keychain.HMAC_SERVICE, create=True)
    # Same keyed SQLCipher engine open_db builds, stopped at 0003.
    eng = create_engine(f"sqlite:///{path}", module=sqlcipher)

    def keyed(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute(f"PRAGMA key = \"x'{key}'\"")

    event.listen(eng, "connect", keyed)
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


def test_migration_0009_round_trips_registry_rows(tmp_path: Path) -> None:
    """0008 -> 0009 keeps synthetic registry rows (not reverted), and 0009 ->
    0008 drops only what 0009 added (#110)."""
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine, inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0013"]
    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    rows = "SELECT id, status FROM subscription_entry"
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0008")
        for stmt in (
            "INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')",
            "INSERT INTO merchant (id, user_id, name) VALUES (1, 1, 'SYNTH GYM')",
            "INSERT INTO subscription_entry (user_id, merchant_id, source, status,"
            " evidence_on, created_at) VALUES (1, 1, 'user', 'not_subscription',"
            " '2026-01-01', '2026-01-01')",
        ):
            conn.execute(text(stmt))
        before = conn.execute(text(rows)).all()

        command.upgrade(cfg, "0009")
        assert conn.execute(text(rows)).all() == before
        reverted = conn.execute(text("SELECT reverted_at FROM subscription_entry"))
        assert reverted.all() == [(None,)]
        assert "review_decision" in inspect(conn).get_table_names()

        command.downgrade(cfg, "0008")
        assert conn.execute(text(rows)).all() == before
        insp = inspect(conn)
        assert "review_decision" not in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("subscription_entry")}
        assert "reverted_at" not in cols
    eng.dispose()


def test_migration_0010_keeps_category_corrections_and_drops_only_splits(
    tmp_path: Path,
) -> None:
    """0009 -> 0010 keeps synthetic Corrections as kind "category"; 0010 -> 0009
    drops the split rows and table only (#123)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    rows = "SELECT id, merchant_id, category_id, reverted_at FROM correction"
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0009")
        for stmt in (
            "INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')",
            "INSERT INTO category (id, user_id, name, taxonomy_version)"
            " VALUES (1, 1, 'Groceries', 2)",
            "INSERT INTO merchant (id, user_id, name) VALUES (1, 1, 'SYNTH SHOP')",
            "INSERT INTO correction (user_id, merchant_id, category_id, created_at)"
            " VALUES (1, 1, 1, '2026-01-01')",
        ):
            conn.execute(text(stmt))
        before = conn.execute(text(rows)).all()

        command.upgrade(cfg, "0010")
        assert conn.execute(text(rows)).all() == before
        kinds = conn.execute(text("SELECT kind FROM correction")).all()
        assert kinds == [("category",)]
        with pytest.raises(Exception, match="CHECK"):  # a category row needs one
            conn.execute(
                text(
                    "INSERT INTO correction (user_id, merchant_id, created_at)"
                    " VALUES (1, 1, '2026-01-02')"
                )
            )

        command.downgrade(cfg, "0009")
        assert conn.execute(text(rows)).all() == before
        insp = inspect(conn)
        assert "split_part" not in insp.get_table_names()
        assert "kind" not in {c["name"] for c in insp.get_columns("correction")}
    eng.dispose()


def test_migration_0011_adds_and_drops_only_the_note_table(tmp_path: Path) -> None:
    """0010 -> 0011 adds `note` with its length check; 0011 -> 0010 drops it (#124)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0010")
        tables = set(inspect(conn).get_table_names())
        command.upgrade(cfg, "0011")
        assert set(inspect(conn).get_table_names()) == tables | {"note"}
        with pytest.raises(Exception, match="CHECK"):  # over 140 characters
            conn.execute(
                text(
                    "INSERT INTO note (user_id, transaction_id, text, created_at)"
                    " VALUES (1, 1, :t, '2026-01-01')"
                ),
                {"t": "x" * 141},
            )
        with pytest.raises(Exception, match="CHECK"):  # a part note needs its Split
            conn.execute(
                text(
                    "INSERT INTO note (user_id, transaction_id, part, text, created_at)"
                    " VALUES (1, 1, 0, 'synthetic', '2026-01-01')"
                )
            )
        command.downgrade(cfg, "0010")
        assert set(inspect(conn).get_table_names()) == tables
    eng.dispose()


def test_migration_0012_adds_and_drops_the_always_ask_flag(tmp_path: Path) -> None:
    """0011 -> 0012 adds merchant.always_ask, keeping Merchant rows, with only
    'user' or 'suggestion' as its source; 0012 -> 0011 drops it (#125)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0011")
        conn.execute(text("INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')"))
        conn.execute(text("INSERT INTO merchant (user_id, name) VALUES (1, 'SYNTH')"))
        command.upgrade(cfg, "0012")
        rows = conn.execute(text("SELECT name, always_ask FROM merchant")).all()
        assert [tuple(r) for r in rows] == [("SYNTH", None)]
        conn.execute(text("UPDATE merchant SET always_ask = 'suggestion'"))
        with pytest.raises(Exception, match="CHECK"):
            conn.execute(text("UPDATE merchant SET always_ask = 'categoriser'"))
        command.downgrade(cfg, "0011")
        cols = {c["name"] for c in inspect(conn).get_columns("merchant")}
        assert "always_ask" not in cols
        names = conn.execute(text("SELECT name FROM merchant")).scalars().all()
        assert names == ["SYNTH"]
    eng.dispose()


def test_migration_0012_keeps_rows_that_reference_a_merchant(tmp_path: Path) -> None:
    """Verifier #170: the app migrates with foreign keys on (`open_db`), so
    recreating `merchant` must not fail or cascade on rows pointing at it."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")

    def fk_on(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    event.listen(eng, "connect", fk_on)
    sub = "SELECT merchant_id FROM subscription_entry"
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0011")
        conn.execute(text("INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')"))
        conn.execute(
            text("INSERT INTO merchant (id, user_id, name) VALUES (7, 1, 'SYNTH')")
        )
        conn.execute(
            text(
                "INSERT INTO subscription_entry (user_id, merchant_id, source, status,"
                " evidence_on, created_at) VALUES (1, 7, 'user', 'not_subscription',"
                " '2026-01-01', '2026-01-01')"
            )
        )
    for target in ("0012", "0011", "0012"):
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            if target == "0011":
                command.downgrade(cfg, target)
            else:
                command.upgrade(cfg, target)
            assert conn.execute(text(sub)).scalars().all() == [7]
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
    eng.dispose()


def test_migration_0013_adds_fx_and_keeps_rows_that_reference_a_transaction(
    tmp_path: Path,
) -> None:
    """0013 (#156) adds transaction.gbp_minor in place and a new fx_rate table:
    with foreign keys on, rows pointing at a Transaction survive up, down, up."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, event, inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")

    def fk_on(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    event.listen(eng, "connect", fk_on)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0012")
        for sql in (
            "INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')",
            "INSERT INTO bank (id, user_id, name) VALUES (1, 1, 'Synth')",
            "INSERT INTO account (id, user_id, bank_id, external_ref, name, kind,"
            " masked_number, currency) VALUES (1, 1, 1, 'r', 'A', 'current', 'x',"
            " 'EUR')",
            'INSERT INTO "transaction" (id, user_id, account_id, posted_on,'
            " bank_type, description, amount_minor, currency, status) VALUES"
            " (9, 1, 1, '2026-01-02', 'CARD', 'SYNTH', -1000, 'EUR', 'completed')",
            "INSERT INTO note (user_id, transaction_id, text, created_at)"
            " VALUES (1, 9, 'synthetic', '2026-01-02')",
        ):
            conn.execute(text(sql))
    notes = "SELECT transaction_id FROM note"
    for target in ("0013", "0012", "0013"):
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            if target == "0012":
                command.downgrade(cfg, target)
            else:
                command.upgrade(cfg, target)
            assert conn.execute(text(notes)).scalars().all() == [9]
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
            tables = inspect(conn).get_table_names()
            cols = {c["name"] for c in inspect(conn).get_columns("transaction")}
            assert ("fx_rate" in tables) == (target == "0013")
            assert ("gbp_minor" in cols) == (target == "0013")
    with eng.begin() as conn:
        assert conn.execute(text('SELECT gbp_minor FROM "transaction"')).all() == [
            (None,)
        ]
        add = (
            "INSERT INTO fx_rate (user_id, currency, rate, rate_on, created_at)"
            " VALUES (1, 'EUR', :r, '2026-01-01', '2026-01-01')"
        )
        conn.execute(text(add), {"r": "0.85"})
        with pytest.raises(Exception, match="UNIQUE"):
            conn.execute(text(add), {"r": "0.9"})
    eng.dispose()
