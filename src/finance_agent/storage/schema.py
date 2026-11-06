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
    # keyed ref of the SimpleFIN account id once synced or linked (ADR 0014)
    Column("provider_ref", String(64)),
    UniqueConstraint("user_id", "external_ref"),
    Index("ux_account_provider_ref", "user_id", "provider_ref", unique=True),
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
    Column("running_balance_minor", BigInteger),  # NULL for synced rows
    Column("status", String(16), nullable=False),
    # "csv" or "simplefin": the source that created it (ADR 0014 matching)
    Column("origin", String(16), nullable=False, server_default="csv"),
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

# ADR 0014 identity: (Account, provider transaction ID), raw synced fields as
# first received. Points at the Transaction it created or was matched to.
synced_key = Table(
    "synced_key",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("account_id", Integer, ForeignKey("account.id"), primary_key=True),
    Column("provider_id", String(256), primary_key=True),
    Column("transaction_id", Integer, ForeignKey("transaction.id"), nullable=False),
    Column("posted_ts", BigInteger, nullable=False),  # unix seconds
    Column("transacted_ts", BigInteger),
    Column("amount_minor", BigInteger, nullable=False),
    Column("description", String(256), nullable=False),
)

# ADR 0014 Reconciliation: the first sync's balance, then one row per non-zero Gap.
sync_anchor = Table(
    "sync_anchor",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("account_id", Integer, ForeignKey("account.id"), primary_key=True),
    Column("balance_minor", BigInteger, nullable=False),
    Column("balance_ts", BigInteger, nullable=False),
)

sync_gap = Table(
    "sync_gap",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("account_id", Integer, ForeignKey("account.id"), nullable=False),
    Column(
        "statement_import_id",
        Integer,
        ForeignKey("statement_import.id"),
        nullable=False,
    ),
    Column("balance_ts", BigInteger, nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
)

# ADR 0014 Possible Duplicates from the provider: (a) one purchase under several
# IDs in one response, (b) a re-issued ID. Kept and surfaced, never merged.
possible_duplicate = Table(
    "possible_duplicate",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("group_id", Integer, primary_key=True),
    Column("transaction_id", Integer, ForeignKey("transaction.id"), primary_key=True),
    Column("account_id", Integer, ForeignKey("account.id"), nullable=False),
    Column(
        "statement_import_id",
        Integer,
        ForeignKey("statement_import.id"),
        nullable=False,
    ),
)
