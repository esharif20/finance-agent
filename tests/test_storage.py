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


def _context(conn: Any) -> MigrationContext:
    """The FTS5 table and its shadow tables (#253) live outside the metadata."""

    def ours(name: str | None, type_: str, _parent: Any) -> bool:
        return not (type_ == "table" and (name or "").startswith("search_fts"))

    return MigrationContext.configure(conn, opts={"include_name": ours})


def test_migrations_produce_exactly_the_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(_context(conn), schema.metadata)
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
        assert compare_metadata(_context(conn), schema.metadata) == []
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
    assert ScriptDirectory.from_config(cfg).get_heads() == ["0027"]
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


def test_migration_0014_adds_embeddings_and_keeps_rows_that_reference_them(
    tmp_path: Path,
) -> None:
    """0014 (#113) adds merchant_embedding and its trigger, nothing rebuilt:
    with foreign keys on, a Merchant, its categorised Transaction and its
    vector survive up, down (vector dropped), up; the trigger exists only at
    0014 and still fires after the round trip."""
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
        command.upgrade(cfg, "0013")
        for sql in (
            "INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')",
            "INSERT INTO bank (id, user_id, name) VALUES (1, 1, 'Synth')",
            "INSERT INTO account (id, user_id, bank_id, external_ref, name, kind,"
            " masked_number, currency) VALUES (1, 1, 1, 'r', 'A', 'current', 'x',"
            " 'GBP')",
            'INSERT INTO "transaction" (id, user_id, account_id, posted_on,'
            " bank_type, description, amount_minor, currency, status) VALUES"
            " (9, 1, 1, '2026-01-02', 'CARD', 'SYNTH CAFE', -1000, 'GBP',"
            " 'completed')",
            "INSERT INTO merchant (id, user_id, name) VALUES (5, 1, 'SYNTH CAFE')",
            "INSERT INTO categorisation (user_id, transaction_id, kind, merchant_id,"
            " source, taxonomy_version) VALUES (1, 9, 'Spending', 5, 'default', 2)",
        ):
            conn.execute(text(sql))
    vec = (
        "INSERT INTO merchant_embedding (user_id, merchant_id, model, dims, vector)"
        " VALUES (1, 5, 'm', 1, x'0100')"
    )
    trig = "SELECT name FROM sqlite_master WHERE type = 'trigger'"
    for target in ("0014", "0013", "0014"):
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            if target == "0013":
                command.downgrade(cfg, target)
            else:
                command.upgrade(cfg, target)
                conn.execute(text(vec))
            assert conn.execute(text("SELECT id FROM merchant")).scalars().all() == [5]
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
            assert ("merchant_embedding" in inspect(conn).get_table_names()) == (
                target == "0014"
            )
            indexes = {i["name"] for i in inspect(conn).get_indexes("categorisation")}
            assert ("ix_categorisation_merchant" in indexes) == (target == "0014")
            triggers = conn.execute(text(trig)).scalars().all()
            assert triggers == (
                ["merchant_embedding_source_gone"] if target == "0014" else []
            )
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM categorisation"))
        conn.execute(text('DELETE FROM "transaction"'))
        assert conn.execute(text("SELECT * FROM merchant_embedding")).all() == []
    eng.dispose()


def _fk_engine(tmp_path: Path) -> Any:
    from sqlalchemy import create_engine, event

    eng = create_engine(f"sqlite:///{tmp_path / 'ledger.db'}")

    def fk_on(dbapi_conn: Any, _record: Any) -> None:
        dbapi_conn.execute("PRAGMA foreign_keys = ON")

    event.listen(eng, "connect", fk_on)
    return eng


_NOTED_TXN = (
    "INSERT INTO user (id, created_at) VALUES (1, '2026-01-01')",
    "INSERT INTO bank (id, user_id, name) VALUES (1, 1, 'Synth')",
    "INSERT INTO account (id, user_id, bank_id, external_ref, name, kind,"
    " masked_number, currency) VALUES (1, 1, 1, 'r', 'A', 'current', 'x', 'GBP')",
    'INSERT INTO "transaction" (id, user_id, account_id, posted_on,'
    " bank_type, description, amount_minor, currency, status) VALUES"
    " (9, 1, 1, '2026-01-02', 'CARD', 'SYNTH', -1000, 'GBP', 'completed')",
    "INSERT INTO note (user_id, transaction_id, text, created_at)"
    " VALUES (1, 9, 'synthetic', '2026-01-02')",
)


def round_trip(
    tmp_path: Path, before: str, after: str, added: set[str], rows: tuple[str, ...]
) -> Any:
    """Migrate `before` -> `after` -> `before` -> `after` with foreign keys on
    and a noted Transaction present; `rows` are inserted at each `after`."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    eng = _fk_engine(tmp_path)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, before)
        for sql in _NOTED_TXN:
            conn.execute(text(sql))
    for target in (after, before, after):
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            if target == before:
                command.downgrade(cfg, target)
            else:
                command.upgrade(cfg, target)
                for sql in rows:
                    conn.execute(text(sql))
            assert conn.execute(text("SELECT transaction_id FROM note")).all() == [(9,)]
            assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
            tables = set(inspect(conn).get_table_names())
            assert (tables & added) == (added if target == after else set())
    return eng


def test_migration_0015_adds_proposals_and_keeps_rows_that_reference_them(
    tmp_path: Path,
) -> None:
    """0015 (#138) adds proposal, memory and feedback, nothing rebuilt; a
    Memory and a feedback row point at Proposals, which they then pin."""
    from sqlalchemy import text

    rows = (
        "INSERT INTO proposal (id, user_id, kind, payload, status, created_at)"
        " VALUES (1, 1, 'memory', '{}', 'confirmed', '2026-01-03'),"
        " (2, 1, 'correction', '{}', 'rejected', '2026-01-03')",
        "INSERT INTO memory (user_id, proposal_id, type, text, tags, turn_id,"
        " quote, said_on, created_at) VALUES (1, 1, 'goal', 'synthetic', '[]',"
        " 't', 'q', '2026-01-03', '2026-01-03')",
        "INSERT INTO feedback (user_id, proposal_id, kind, signal, created_at)"
        " VALUES (1, 2, 'correction', 'rejected', '2026-01-03')",
    )
    eng = round_trip(tmp_path, "0014", "0015", {"proposal", "memory", "feedback"}, rows)
    with eng.begin() as conn, pytest.raises(Exception, match="FOREIGN KEY"):
        conn.execute(text("DELETE FROM proposal WHERE id = 1"))
    eng.dispose()


def test_migration_0016_adds_checkpoints_and_keeps_rows_that_reference_them(
    tmp_path: Path,
) -> None:
    """0016 (#139) adds the two checkpoint tables, nothing rebuilt; a Memory
    pointing at its Proposal survives the round trip."""
    rows = (
        "INSERT OR IGNORE INTO proposal (id, user_id, kind, payload, status,"
        " created_at) VALUES (1, 1, 'memory', '{}', 'confirmed', '2026-01-03')",
        "INSERT OR IGNORE INTO memory (id, user_id, proposal_id, type, text, tags,"
        " turn_id, quote, said_on, created_at) VALUES (1, 1, 1, 'goal', 's', '[]',"
        " 't', 'q', '2026-01-03', '2026-01-03')",
        "INSERT INTO assistant_checkpoint (user_id, thread_id, checkpoint_ns,"
        " checkpoint_id, checkpoint_type, checkpoint, metadata_type, metadata,"
        " created_at) VALUES (1, 't', '', 'c', 'msgpack', x'00', 'msgpack', x'00',"
        " '2026-01-03')",
        "INSERT INTO assistant_checkpoint_write (user_id, thread_id, checkpoint_ns,"
        " checkpoint_id, task_id, idx, channel, value_type, value, task_path)"
        " VALUES (1, 't', '', 'c', 'k', 0, 'reply', 'msgpack', x'00', '')",
    )
    added = {"assistant_checkpoint", "assistant_checkpoint_write"}
    round_trip(tmp_path, "0015", "0016", added, rows).dispose()


def test_migration_0017_adds_the_answer_cache_and_keeps_rows_that_reference_them(
    tmp_path: Path,
) -> None:
    """0017 (#140) adds answer_cache, nothing rebuilt; a Memory pointing at its
    Proposal survives the round trip."""
    rows = (
        "INSERT OR IGNORE INTO proposal (id, user_id, kind, payload, status,"
        " created_at) VALUES (1, 1, 'memory', '{}', 'confirmed', '2026-01-03')",
        "INSERT OR IGNORE INTO memory (id, user_id, proposal_id, type, text, tags,"
        " turn_id, quote, said_on, created_at) VALUES (1, 1, 1, 'goal', 's', '[]',"
        " 't', 'q', '2026-01-03', '2026-01-03')",
        "INSERT INTO answer_cache (user_id, key, ledger_version, reply, evidence,"
        " created_at) VALUES (1, 'k', 0, 'synthetic', '[]', '2026-01-03')",
    )
    round_trip(tmp_path, "0016", "0017", {"answer_cache"}, rows).dispose()


def test_migration_0018_adds_insights_and_keeps_rows_that_reference_them(
    tmp_path: Path,
) -> None:
    """0018 (#150) adds insight and insight_feedback, nothing rebuilt; a rating
    points at its Insight, which it then pins."""
    from sqlalchemy import text

    rows = (
        "INSERT INTO insight (id, user_id, type, key, category, raised_on,"
        " impact_minor, confidence, evidence, created_at) VALUES (1, 1,"
        " 'recurring', 'SYNTH GYM', 'Health / Gym', '2026-01-03', 1200, 90,"
        " '[9]', '2026-01-03')",
        "INSERT INTO insight_feedback (user_id, insight_id, insight_type,"
        " category, signal, created_at) VALUES (1, 1, 'recurring',"
        " 'Health / Gym', 'up', '2026-01-03')",
    )
    added = {"insight", "insight_feedback"}
    eng = round_trip(tmp_path, "0017", "0018", added, rows)
    with eng.begin() as conn, pytest.raises(Exception, match="FOREIGN KEY"):
        conn.execute(text("DELETE FROM insight WHERE id = 1"))
    eng.dispose()


def test_migration_0019_adds_regret_answers_which_go_with_their_transaction(
    tmp_path: Path,
) -> None:
    """0019 (#152) adds regret_answer, nothing rebuilt; an answer points at its
    Transaction and is deleted with it (as when a synced copy merges)."""
    from sqlalchemy import text

    rows = (
        "INSERT INTO regret_answer (user_id, transaction_id, week, worth_it,"
        " created_at) VALUES (1, 9, '2026-01-05', 1, '2026-01-05')",
    )
    eng = round_trip(tmp_path, "0018", "0019", {"regret_answer"}, rows)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM note"))
        conn.execute(text('DELETE FROM "transaction" WHERE id = 9'))
        assert conn.execute(text("SELECT * FROM regret_answer")).all() == []
    eng.dispose()


def test_migration_0020_adds_merges_and_alerts_which_go_with_their_source(
    tmp_path: Path,
) -> None:
    """0020 (#108, #158) adds two tables, nothing rebuilt (so no table another
    references is copied with foreign keys on); a merge points at its
    Transaction and an alert at its Merchant, and each is deleted with it."""
    from sqlalchemy import text

    rows = (
        "INSERT OR IGNORE INTO merchant (id, user_id, name) VALUES (5, 1, 'SYNTH')",
        "INSERT INTO duplicate_merge (user_id, transaction_id, created_at)"
        " VALUES (1, 9, '2026-01-05')",
        "INSERT INTO subscription_alert (user_id, merchant_id, code, state,"
        " alerted_at) VALUES (1, 5, 'price_rise', '2026-01-04', '2026-01-05')",
    )
    added = {"duplicate_merge", "subscription_alert"}
    eng = round_trip(tmp_path, "0019", "0020", added, rows)
    with eng.begin() as conn, pytest.raises(Exception, match="UNIQUE"):
        conn.execute(text(rows[2]))  # one alert per state
    with eng.begin() as conn, pytest.raises(Exception, match="CHECK"):
        conn.execute(text(rows[2].replace("price_rise", "anything")))
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM note"))
        conn.execute(text('DELETE FROM "transaction" WHERE id = 9'))
        assert conn.execute(text("SELECT * FROM duplicate_merge")).all() == []
        conn.execute(text("DELETE FROM merchant WHERE id = 5"))
        assert conn.execute(text("SELECT * FROM subscription_alert")).all() == []
    eng.dispose()


def test_migration_0021_adds_and_drops_only_the_registry_trigger(
    tmp_path: Path,
) -> None:
    """0021 (#132) adds one trigger, nothing rebuilt: at 0021 a Merchant takes
    its registry history with it; at 0020 the foreign key still refuses."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    rows = (
        "INSERT OR IGNORE INTO merchant (id, user_id, name) VALUES (5, 1, 'SYNTH')",
        "INSERT INTO subscription_entry (user_id, merchant_id, source, kind,"
        " group_name, status, reason, evidence_on, created_at) VALUES (1, 5,"
        " 'user', 'subscription', 'Streaming', 'active', 'user_added',"
        " '2026-01-05', '2026-01-05')",
    )
    trig = "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = :n"
    name = {"n": "subscription_entry_merchant_gone"}
    count = "SELECT count(*) FROM subscription_entry"
    eng = round_trip(tmp_path, "0020", "0021", set(), rows)
    with eng.begin() as conn:
        assert conn.execute(text(trig), name).all() == [(name["n"],)]
        assert conn.execute(text(count)).scalar() == 2  # both upgrades' rows kept
    cfg = Config()
    cfg.set_main_option("script_location", str(storage._MIGRATIONS))  # pyright: ignore[reportPrivateUsage]
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0020")
        assert conn.execute(text(trig), name).all() == []
    with eng.begin() as conn, pytest.raises(Exception, match="FOREIGN KEY"):
        conn.execute(text("DELETE FROM merchant WHERE id = 5"))
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0021")
        conn.execute(text("DELETE FROM merchant WHERE id = 5"))
        assert conn.execute(text(count)).scalar() == 0
    eng.dispose()


def test_the_registry_trigger_is_present_at_head(engine: Engine) -> None:
    """A later migration that rebuilds `merchant` drops its triggers silently;
    this catches that before the registry can outlive its Merchant."""
    from sqlalchemy import text

    sql = (
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='merchant'"
        " AND name NOT LIKE 'search_%'"  # the search index's own (#253)
    )
    with engine.connect() as conn:
        got = conn.execute(text(sql)).scalars().all()
    assert len(got) == 1 and "subscription_entry" in got[0]


def test_migration_0022_adds_transfer_rejections_which_go_with_their_transaction(
    tmp_path: Path,
) -> None:
    """0022 (#215 C) adds transfer_rejection, nothing rebuilt; a rejection
    points at its Transaction and is deleted with it."""
    from sqlalchemy import text

    rows = (
        "INSERT INTO transfer_rejection (user_id, transaction_id, created_at)"
        " VALUES (1, 9, '2026-01-05')",
    )
    eng = round_trip(tmp_path, "0021", "0022", {"transfer_rejection"}, rows)
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM note"))
        conn.execute(text('DELETE FROM "transaction" WHERE id = 9'))
        assert conn.execute(text("SELECT * FROM transfer_rejection")).all() == []
    eng.dispose()


def test_migration_0023_adds_predictions_which_go_with_their_merchant(
    tmp_path: Path,
) -> None:
    """0023 (#215 B) adds category_prediction and model_suspension, nothing
    rebuilt; a prediction is deleted with its Merchant."""
    from sqlalchemy import text

    rows = (
        "INSERT OR IGNORE INTO merchant (id, user_id, name) VALUES (5, 1, 'SYNTH')",
        "INSERT OR IGNORE INTO category (id, user_id, name, taxonomy_version)"
        " VALUES (7, 1, 'Synth', 2)",
        "INSERT INTO category_prediction (user_id, model, merchant_id,"
        " category_id, score, created_at) VALUES (1, 'm', 5, 7, 900, '2026-01-05')",
        "INSERT INTO model_suspension (user_id, model, decisions, suspended_at)"
        " VALUES (1, 'm', 50, '2026-01-05')",
    )
    eng = round_trip(
        tmp_path, "0022", "0023", {"category_prediction", "model_suspension"}, rows
    )
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM merchant WHERE id = 5"))
        assert conn.execute(text("SELECT * FROM category_prediction")).all() == []
    eng.dispose()


def test_migration_0024_adds_a_per_user_settings_table(tmp_path: Path) -> None:
    """0024 adds assistant_setting, nothing rebuilt: one row per user, and
    only a known choice."""
    from sqlalchemy import text

    insert = (
        "INSERT INTO assistant_setting (user_id, model, updated_at)"
        " VALUES (1, '{}', '2026-01-05')"
    )
    eng = round_trip(
        tmp_path, "0023", "0024", {"assistant_setting"}, (insert.format("careful"),)
    )
    for bad in ("huge", "fast"):  # an unknown choice; a second row for the user
        with eng.begin() as conn, pytest.raises(Exception, match="CHECK|UNIQUE"):
            conn.execute(text(insert.format(bad)))
    eng.dispose()


def test_migration_0025_adds_goal_fields_and_keeps_memories(tmp_path: Path) -> None:
    """0025 (#257) adds four nullable memory columns, nothing rebuilt on the
    way up; a Memory survives the round trip, and deleting its linked Account
    unlinks it rather than deleting it."""
    from sqlalchemy import inspect, text

    rows = (
        "INSERT OR IGNORE INTO account (id, user_id, bank_id, external_ref, name,"
        " kind, masked_number, currency) VALUES (2, 1, 1, 's', 'B', 'savings',"
        " 'y', 'GBP')",
        "INSERT OR IGNORE INTO proposal (id, user_id, kind, payload, status,"
        " created_at) VALUES (1, 1, 'memory', '{}', 'confirmed', '2026-01-05')",
        "INSERT OR IGNORE INTO memory (id, user_id, proposal_id, type, text, tags,"
        " turn_id, quote, said_on, created_at) VALUES (1, 1, 1, 'goal',"
        " 'synthetic car fund', '[]', 't', 'q', '2026-01-05', '2026-01-05')",
        "UPDATE memory SET amount_minor = 300000, due_on = '2027-06-30',"
        " priority = 1, linked_account_id = 2 WHERE id = 1",
    )
    eng = round_trip(tmp_path, "0024", "0025", set(), rows)
    with eng.begin() as conn:
        cols = {c["name"] for c in inspect(conn).get_columns("memory")}
        assert {"amount_minor", "due_on", "priority", "linked_account_id"} <= cols
        conn.execute(text("DELETE FROM account WHERE id = 2"))
        got = conn.execute(
            text("SELECT text, amount_minor, linked_account_id FROM memory")
        ).all()
        assert got == [("synthetic car fund", 300000, None)]
        with pytest.raises(Exception, match="CHECK"):  # the rebuild kept checks
            conn.execute(text("UPDATE memory SET type = 'fact'"))
    eng.dispose()
