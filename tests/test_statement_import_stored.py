"""M1.8: a Statement Import is stored unchanged (original bytes, exactly)."""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine, select

from finance_agent import identity
from finance_agent.ledger import Ledger
from finance_agent.storage import schema
from synthetic_lloyds import generate_histories, render_export


def test_the_stored_statement_import_equals_the_original_file_exactly(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    h, _ = generate_histories(seed=8, days=60)
    path = write_export(render_export(h, 0, 30).text)
    original = path.read_bytes()
    with engine.begin() as conn:
        Ledger(conn, identity.current_user()).import_files([path])
        stored = conn.execute(select(schema.statement_import.c.original_bytes)).all()
    assert [bytes(r[0]) for r in stored] == [original]
