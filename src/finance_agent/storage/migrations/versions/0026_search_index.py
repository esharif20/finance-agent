"""Search index (#253, migration 1 of #251): an FTS5 table `search_fts` and an
int16 vector table `text_embedding` over Transactions, Merchants, live Notes,
live Split parts and Memories, kept in step by triggers

A document's id is its FTS rowid: kind * 2**40 + the source row's id (a Split
part's is correction id * 10 + ordinal), so every trigger deletes by rowid.
Every trigger that removes a document removes its vector too. FTS5's
`secure-delete` option drops a deleted row's tokens from the index, and
`PRAGMA secure_delete` zeroes the freed pages, so a purged Note leaves nothing.

Revision ID: 0026
Revises: 0025
"""

# ruff: noqa: S608 - the SQL is built from the constants here, never input

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

BASE = 1 << 40  # keep in step with ledger/search_index.py
TRANSACTION, MERCHANT, NOTE, SPLIT, MEMORY = (k * BASE for k in range(1, 6))


def _doc(rowid: str, body: str, user: str, where: str = "", source: str = "") -> str:
    return (
        f"INSERT INTO search_fts(rowid, body, user_id) SELECT {rowid}, {body}, {user}"
        f"{' FROM ' + source if source else ''}{' WHERE ' + where if where else ''};"
    )


def _drop(cond: str) -> str:
    """Delete documents (`cond` on {id}) and their vectors, vectors first: a
    `cond` may read search_fts."""
    return (
        f"DELETE FROM text_embedding WHERE {cond.format(id='doc_id')};"
        f"DELETE FROM search_fts WHERE {cond.format(id='rowid')};"
    )


def _split_body(part: str) -> str:
    return (
        f"(SELECT name FROM category WHERE id = {part}.category_id) || ' ' || "
        '(SELECT t.description FROM correction k JOIN "transaction" t '
        f"ON t.id = k.transaction_id WHERE k.id = {part}.correction_id)"
    )


_MERCHANT_GONE = (
    f"{{id}} BETWEEN {MERCHANT} AND {NOTE - 1} AND {{id}} IN ("
    f"SELECT rowid FROM search_fts WHERE rowid BETWEEN {MERCHANT} AND {NOTE - 1} "
    f"AND user_id = OLD.user_id AND NOT EXISTS (SELECT 1 FROM categorisation c "
    f"WHERE c.merchant_id = search_fts.rowid - {MERCHANT}))"
)

# (name, event, body). A Merchant is indexed once a Transaction names it and,
# like its embedding (0014), goes with the last Transaction naming it: an
# Import or Account deletion. `categorise.run` rewrites categorisation without
# deleting Transactions, so its Merchants' documents and vectors stay.
TRIGGERS: list[tuple[str, str, str]] = [
    (
        "search_transaction_added",
        'AFTER INSERT ON "transaction"',
        _doc(f"{TRANSACTION} + NEW.id", "NEW.description", "NEW.user_id"),
    ),
    (
        "search_transaction_gone",
        'AFTER DELETE ON "transaction"',
        _drop(f"{{id}} = {TRANSACTION} + OLD.id") + _drop(_MERCHANT_GONE),
    ),
    (
        "search_merchant_named",
        "AFTER INSERT ON categorisation WHEN NEW.merchant_id IS NOT NULL "
        f"AND NOT EXISTS (SELECT 1 FROM search_fts WHERE rowid = "
        f"{MERCHANT} + NEW.merchant_id)",
        _doc(
            f"{MERCHANT} + id",
            "name",
            "user_id",
            "id = NEW.merchant_id",
            "merchant",
        ),
    ),
    (
        "search_merchant_renamed",
        "AFTER UPDATE OF name ON merchant WHEN NEW.name IS NOT OLD.name "
        f"AND EXISTS (SELECT 1 FROM search_fts WHERE rowid = {MERCHANT} + NEW.id)",
        _drop(f"{{id}} = {MERCHANT} + OLD.id")
        + _doc(f"{MERCHANT} + NEW.id", "NEW.name", "NEW.user_id"),
    ),
    (
        "search_merchant_gone",
        "AFTER DELETE ON merchant",
        _drop(f"{{id}} = {MERCHANT} + OLD.id"),
    ),
    (
        "search_note_added",
        "AFTER INSERT ON note WHEN NEW.superseded_at IS NULL",
        _doc(f"{NOTE} + NEW.id", "NEW.text", "NEW.user_id"),
    ),
    (
        "search_note_superseded",
        "AFTER UPDATE OF superseded_at ON note "
        "WHEN NEW.superseded_at IS NOT NULL AND OLD.superseded_at IS NULL",
        _drop(f"{{id}} = {NOTE} + OLD.id"),
    ),
    (
        "search_note_gone",
        "AFTER DELETE ON note",
        _drop(f"{{id}} = {NOTE} + OLD.id"),
    ),
    (
        "search_split_part_added",
        "AFTER INSERT ON split_part",
        _doc(
            f"{SPLIT} + NEW.correction_id * 10 + NEW.ordinal",
            _split_body("NEW"),
            "NEW.user_id",
            "(SELECT reverted_at FROM correction WHERE id = NEW.correction_id) IS NULL",
        ),
    ),
    (
        "search_split_reverted",
        "AFTER UPDATE OF reverted_at ON correction WHEN NEW.kind = 'split' "
        "AND NEW.reverted_at IS NOT NULL AND OLD.reverted_at IS NULL",
        _drop(f"{{id}} BETWEEN {SPLIT} + OLD.id * 10 AND {SPLIT} + OLD.id * 10 + 9"),
    ),
    (
        "search_split_part_gone",
        "AFTER DELETE ON split_part",
        _drop(f"{{id}} = {SPLIT} + OLD.correction_id * 10 + OLD.ordinal"),
    ),
    (
        "search_memory_added",
        "AFTER INSERT ON memory",
        _doc(f"{MEMORY} + NEW.id", "NEW.text", "NEW.user_id"),
    ),
    (
        "search_memory_changed",
        "AFTER UPDATE OF text ON memory WHEN NEW.text IS NOT OLD.text",
        _drop(f"{{id}} = {MEMORY} + OLD.id")
        + _doc(f"{MEMORY} + NEW.id", "NEW.text", "NEW.user_id"),
    ),
    (
        "search_memory_gone",
        "AFTER DELETE ON memory",
        _drop(f"{{id}} = {MEMORY} + OLD.id"),
    ),
]

BACKFILL = [
    _doc(f"{TRANSACTION} + id", "description", "user_id", source='"transaction"'),
    _doc(
        f"{MERCHANT} + id",
        "name",
        "user_id",
        "id IN (SELECT merchant_id FROM categorisation)",
        "merchant",
    ),
    _doc(f"{NOTE} + id", "text", "user_id", "superseded_at IS NULL", "note"),
    _doc(
        f"{SPLIT} + p.correction_id * 10 + p.ordinal",
        _split_body("p"),
        "p.user_id",
        "k.id = p.correction_id AND k.reverted_at IS NULL",
        "split_part p, correction k",
    ),
    _doc(f"{MEMORY} + id", "text", "user_id", source="memory"),
]


def upgrade() -> None:
    op.create_table(
        "text_embedding",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("doc_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("dims", sa.Integer, nullable=False),
        sa.Column("vector", sa.LargeBinary, nullable=False),
    )
    # shortcut: SQLite FTS5 and trigger syntax; a Postgres move (ADR 0003)
    # needs tsvector and trigger functions instead.
    if op.get_bind().dialect.name != "sqlite":
        return
    op.execute(
        "CREATE VIRTUAL TABLE search_fts USING fts5(body, user_id UNINDEXED, "
        "tokenize = 'unicode61 remove_diacritics 2')"
    )
    op.execute("INSERT INTO search_fts(search_fts, rank) VALUES ('secure-delete', 1)")
    for sql in BACKFILL:
        op.execute(sql)
    for name, event, body in TRIGGERS:
        op.execute(f"CREATE TRIGGER {name} {event} BEGIN {body} END")


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        for name, _, _ in TRIGGERS:
            op.execute(f"DROP TRIGGER IF EXISTS {name}")
        op.execute("DROP TABLE IF EXISTS search_fts")
    op.drop_table("text_embedding")
