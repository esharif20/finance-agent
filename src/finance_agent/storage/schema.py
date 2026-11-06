"""Table definitions. Every table carries `user_id`; money is integer minor units.

Migrations in `migrations/versions/` must produce exactly this schema
(tests/test_storage.py checks for drift).
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)

metadata = MetaData()

user = Table(
    "user",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

bank = Table(
    "bank",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("name", String(64), nullable=False),
    UniqueConstraint("user_id", "name"),
)

account = Table(
    "account",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("bank_id", Integer, ForeignKey("bank.id"), nullable=False),
    # opaque digest from `ingest`; the full account number never leaves it
    Column("external_ref", String(64), nullable=False),
    Column("name", String(128), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("masked_number", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    UniqueConstraint("user_id", "external_ref"),
)

statement_import = Table(
    "statement_import",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("source", String(16), nullable=False),
    Column("file_hash", String(64), nullable=False),
    # shortcut: stored plain until SQLCipher in M3 (ADR 0002); synthetic files only
    Column("original_bytes", LargeBinary, nullable=False),
    Column("imported_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "file_hash"),
)

transaction = Table(
    "transaction",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("account_id", Integer, ForeignKey("account.id"), nullable=False),
    Column("posted_on", Date, nullable=False),
    Column("bank_type", String(16), nullable=False),
    Column("description", String(256), nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("running_balance_minor", BigInteger, nullable=False),
    Column("status", String(16), nullable=False),
    # ADR 0006 identity; not unique: a purchase, refund, same purchase again
    # on one day repeats the key, and both are real Transactions
    Index(
        "ix_transaction_identity",
        "user_id",
        "account_id",
        "posted_on",
        "description",
        "amount_minor",
        "running_balance_minor",
    ),
)

transaction_source = Table(
    "transaction_source",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("transaction_id", Integer, ForeignKey("transaction.id"), primary_key=True),
    Column(
        "statement_import_id",
        Integer,
        ForeignKey("statement_import.id"),
        primary_key=True,
    ),
    Column("row_number", Integer, primary_key=True),
)

# Own-account Transfer pairs: from = the outgoing (negative) leg. Bank rows are
# never deleted or changed; both legs stay and are linked (ADR 0001).
transfer_link = Table(
    "transfer_link",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "from_transaction_id", Integer, ForeignKey("transaction.id"), primary_key=True
    ),
    Column(
        "to_transaction_id",
        Integer,
        ForeignKey("transaction.id"),
        nullable=False,
        unique=True,
    ),
)

# Seeded from `categorise.TAXONOMY` per user and taxonomy version.
category = Table(
    "category",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("parent_id", Integer, ForeignKey("category.id")),
    Column("name", String(64), nullable=False),
    Column("taxonomy_version", Integer, nullable=False),
)

# Derived, recomputed by `categorise.run`; never edited by hand (Corrections are
# a separate overlay, ADR 0001).
categorisation = Table(
    "categorisation",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("transaction_id", Integer, ForeignKey("transaction.id"), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("merchant", String(64)),
    Column("category_id", Integer, ForeignKey("category.id")),
    Column("source", String(64), nullable=False),
    Column("taxonomy_version", Integer, nullable=False),
)
