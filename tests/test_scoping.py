"""Scoped.execute runs only statements filtered to its user, joins included."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, insert, select, text, update

from finance_agent import categorise
from finance_agent.ledger import Ledger
from finance_agent.storage import Scoped, UnscopedQuery, schema
from synthetic_lloyds import generate_histories, render_export

t, src, si = schema.transaction, schema.transaction_source, schema.statement_import


@pytest.fixture
def db(engine: Engine) -> Iterator[Scoped]:
    with engine.begin() as conn:
        yield Scoped(conn, 1)


@pytest.mark.parametrize(
    "stmt",
    [
        select(t),  # no filter at all
        select(t.c.id).where(t.c.user_id == 2),  # someone else's
        select(t.c.id).where(t.c.user_id != 1),
        update(t).values(description="x"),
        delete(t),
        insert(t).values(user_id=1),
        text('SELECT * FROM "transaction"'),
    ],
    ids=["bare", "other-user", "not-equal", "update", "delete", "insert", "text"],
)
def test_an_unscoped_statement_is_refused(db: Scoped, stmt: object) -> None:
    with pytest.raises(UnscopedQuery):
        db.execute(stmt)  # type: ignore[arg-type]


def test_a_join_must_scope_the_joined_table_too(db: Scoped) -> None:
    with pytest.raises(UnscopedQuery):
        db.execute(db.select(src).join(si, si.c.id == src.c.statement_import_id))
    db.execute(db.select(src).join(si, db.on(si, si.c.id == src.c.statement_import_id)))
    db.execute(
        db.select(src).outerjoin(si, db.on(si, si.c.id == src.c.statement_import_id))
    )


def test_a_scoped_subquery_does_not_scope_the_outer_table(db: Scoped) -> None:
    inner = db.select(t, t.c.id)
    with pytest.raises(UnscopedQuery):
        db.execute(select(t.c.id).where(t.c.id.in_(inner)))


def test_scoped_statements_run(db: Scoped) -> None:
    db.execute(db.select(t, t.c.id).where(t.c.id > 0))
    db.execute(db.update(t).values(description="x"))
    db.execute(db.delete(t))


def test_joins_never_return_another_users_rows(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    """User 2's rows that point at user 1's rows (bad data, not reachable
    through the Ledger) still show none of user 1's fields."""
    h, _ = generate_histories(seed=5, days=30)
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([write_export(render_export(h, 0, 60).text)])
        categorise.run(conn, 1)
        theirs = Ledger(conn, 2)
        theirs.import_files([write_export(render_export(h, 0, 60).text)])
        categorise.run(conn, 2)
        mine = {m.id for m in Ledger(conn, 1).merchants()}
        some_of_mine = next(iter(mine))
        # point every one of user 2's categorisations at user 1's Merchant
        conn.execute(
            update(schema.categorisation)
            .where(schema.categorisation.c.user_id == 2)
            .values(merchant_id=some_of_mine)
        )
        names = {m.name for m in Ledger(conn, 1).merchants() if m.id == some_of_mine}
        for txn in theirs.transactions():
            c = txn.categorisation
            assert c is not None and c.merchant not in names
        needle = next(iter(names)).lower()
        hits = theirs.search_transactions(query=needle, limit=500).items
        assert all(needle in x.description.lower() for x in hits)
