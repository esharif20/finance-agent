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
    Text,
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
    # "Always ask" (#125): how the user set it, 'user' or 'suggestion'; NULL is
    # off. Never set by the app itself.
    Column("always_ask", String(16)),
    UniqueConstraint("user_id", "name"),
    CheckConstraint(
        "always_ask IN ('user', 'suggestion')", name="ck_merchant_always_ask"
    ),
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
# `kind` "category" sets one Category; "split" (#123) divides one Transaction
# into its `split_part` rows and has no Category of its own.
correction = Table(
    "correction",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("transaction_id", Integer, ForeignKey("transaction.id")),
    Column("merchant_id", Integer, ForeignKey("merchant.id")),
    Column("category_id", Integer, ForeignKey("category.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("reverted_at", DateTime(timezone=True)),
    Column("kind", String(16), nullable=False, server_default="category"),
    CheckConstraint(
        text("(transaction_id IS NULL) != (merchant_id IS NULL)"),
        name="ck_correction_one_target",
    ),
    CheckConstraint(
        text(
            "(kind = 'category' AND category_id IS NOT NULL) OR "
            "(kind = 'split' AND category_id IS NULL AND transaction_id IS NOT NULL)"
        ),
        name="ck_correction_kind",
    ),
)

# The parts of a `split` Correction (#123), written once with it and never
# edited: 2-10 per Correction, non-zero, one sign, adding up to the Transaction
# amount when made. Re-splitting reverts the Correction and writes a new one.
split_part = Table(
    "split_part",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("correction_id", Integer, ForeignKey("correction.id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("amount_minor", BigInteger, nullable=False),
    Column("category_id", Integer, ForeignKey("category.id"), nullable=False),
    CheckConstraint(text("amount_minor != 0"), name="ck_split_part_nonzero"),
    CheckConstraint(text("ordinal BETWEEN 0 AND 9"), name="ck_split_part_ordinal"),
)

# Purpose notes (#124), versioned: editing supersedes the live row and adds a
# new one; deleting purges every row of that target. The target is one
# Transaction (`part` and `split_id` NULL) or part `part` of the Split
# Correction `split_id`: a part note stays with that Split version, so a re-split
# never moves it onto a different purpose. Notes are never logged and never leave
# the Mac by default (AI_DESIGN, DATA_POLICY).
note = Table(
    "note",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("transaction_id", Integer, ForeignKey("transaction.id"), nullable=False),
    Column("part", Integer),
    Column("split_id", Integer, ForeignKey("correction.id")),
    Column("text", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("superseded_at", DateTime(timezone=True)),
    CheckConstraint(text("length(text) BETWEEN 1 AND 140"), name="ck_note_length"),
    CheckConstraint(text("part BETWEEN 0 AND 9"), name="ck_note_part"),
    CheckConstraint(text("(part IS NULL) = (split_id IS NULL)"), name="ck_note_split"),
    Index("ix_note_target", "user_id", "transaction_id"),
)

# Subscription registry (#103), append-only like `correction`: the latest row per
# Merchant is its entry, the rest is the history. `source` is "user" or
# "detection"; once a Merchant has a user row, detection never writes for it.
# A negative entry ("not a subscription") has status `not_subscription`.
subscription_entry = Table(
    "subscription_entry",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("merchant_id", Integer, ForeignKey("merchant.id"), nullable=False),
    Column("source", String(16), nullable=False),
    Column("kind", String(16)),  # "subscription" or "bill"; NULL when negative
    Column("group_name", String(64)),  # a leaf of Subscriptions or Bills
    Column("status", String(16), nullable=False),
    Column("reason", String(16)),  # NULL when negative
    Column("evidence_on", Date, nullable=False),
    Column("charge_count", Integer),
    Column("interval", String(16)),  # "weekly", "monthly" or "annual"
    Column("created_at", DateTime(timezone=True), nullable=False),
    # set when the Review Queue undoes the user decision this row records (#110)
    Column("reverted_at", DateTime(timezone=True)),
    Index("ix_subscription_entry_merchant", "user_id", "merchant_id"),
)

# Review Queue decisions (#110), append-only: an answer or a skip of one item,
# identified by its type and key, holding while the item's inputs still hash
# to `item_version`. `effect_id` is what the answer wrote, read by the item's
# type (a Correction id, a registry row id, or NULL). Undo sets `undone_at`.
review_decision = Table(
    "review_decision",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("item_type", String(32), nullable=False),
    Column("item_key", Text, nullable=False),
    Column("item_version", String(64), nullable=False),
    Column("action", String(16), nullable=False),  # "answer" or "skip"
    Column("effect_id", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("undone_at", DateTime(timezone=True)),
    Index("ix_review_decision_user", "user_id"),
)
