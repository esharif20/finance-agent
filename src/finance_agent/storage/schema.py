"""Table definitions. Every table carries `user_id`; money is integer minor units.

Migrations in `migrations/versions/` must produce exactly this schema
(tests/test_storage.py checks for drift).
"""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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
    text,
)

metadata = MetaData()

user = Table(
    "user",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    # bumped by every import and Correction; caches key off it (ADR 0010)
    Column("ledger_version", Integer, nullable=False, server_default="0"),
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
    Column("row_number", Integer, primary_key=True),  # physical line, for provenance
    # index in the file's Bank order (oldest first); differs from row_number
    # when the Bank lists newest first
    Column("bank_position", Integer, nullable=False, server_default="0"),
)

# derived: recomputed from the Ledger after every import, never edited by hand
reconciliation_gap = Table(
    "reconciliation_gap",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("account_id", Integer, ForeignKey("account.id"), nullable=False),
    Column("from_date", Date, nullable=False),
    Column("to_date", Date, nullable=False),
    # Bank's balance minus the Ledger's: the net of the missing Transactions
    Column("amount_minor", BigInteger, nullable=False),
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

# One row per normalised Merchant name, never deleted, so its id is stable across
# imports and rule changes (Corrections in #10 target it). Seeded Merchants carry
# a default Category; others get NULL until a Correction sets one.
merchant = Table(
    "merchant",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("name", String(64), nullable=False),
    Column("default_category_id", Integer, ForeignKey("category.id")),
    UniqueConstraint("user_id", "name"),
)

# Derived, recomputed by `categorise.run`; never edited by hand (Corrections are
# a separate overlay, ADR 0001). Not part of the duplicate key (ADR 0006).
categorisation = Table(
    "categorisation",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("transaction_id", Integer, ForeignKey("transaction.id"), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("merchant_id", Integer, ForeignKey("merchant.id")),
    Column("category_id", Integer, ForeignKey("category.id")),
    Column("source", String(64), nullable=False),
    Column("taxonomy_version", Integer, nullable=False),
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

# #28 one row per user: freshness, the last failure (class and count only) and
# the bank consent renewal date. `notified` is the last alert already shown, so a
# repeating state is not announced on every scheduled run.
sync_status = Table(
    "sync_status",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("last_success_ts", BigInteger),
    Column("last_failure_ts", BigInteger),
    Column("failure_class", String(64)),
    Column("failures_in_a_row", Integer, nullable=False, server_default="0"),
    Column("consent_renewed_on", Date),
    Column("notified", String(64)),
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

# User Corrections (ADR 0001): never edited or deleted, only reverted, so they
# are an audit trail. Exactly one target: one Transaction or every Transaction
# of a Merchant. Read-time overlay on `categorisation`; rules never see them.
correction = Table(
    "correction",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("transaction_id", Integer, ForeignKey("transaction.id")),
    Column("merchant_id", Integer, ForeignKey("merchant.id")),
    Column("category_id", Integer, ForeignKey("category.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("reverted_at", DateTime(timezone=True)),
    CheckConstraint(
        text("(transaction_id IS NULL) != (merchant_id IS NULL)"),
        name="ck_correction_one_target",
    ),
)
