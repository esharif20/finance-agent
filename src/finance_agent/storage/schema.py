"""Table definitions. Every table carries `user_id`; money is integer minor units.

Migrations in `migrations/versions/` must produce exactly this schema
(tests/test_storage.py checks for drift).
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
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
    # the Bank's own GBP amount on a foreign-currency Transaction (#156)
    Column("gbp_minor", BigInteger),
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
    Index("ix_categorisation_merchant", "merchant_id"),  # the 0014 trigger probes it
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

# FX rates the user entered (#156): GBP per unit of `currency` from `rate_on`,
# a Decimal string (never a float); one per currency and date, re-entering
# replaces it. Rates are never fetched.
fx_rate = Table(
    "fx_rate",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("rate", String(32), nullable=False),
    Column("rate_on", Date, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "currency", "rate_on"),
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

# Derived (#113): one local-model vector per Merchant (its normalised
# descriptor), int16 components. Deleted with its Merchant (cascade), with the
# last Transaction naming it (trigger `merchant_embedding_source_gone`,
# migration 0014) and by `fa forget-embeddings`. No weights, only these rows.
merchant_embedding = Table(
    "merchant_embedding",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "merchant_id",
        Integer,
        ForeignKey("merchant.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("model", String(64), nullable=False),
    Column("dims", Integer, nullable=False),
    Column("vector", LargeBinary, nullable=False),
)

# Search vectors (#253): one int16 vector per search document (`doc_id` is its
# `search_fts` rowid, ledger/search_index.py), same packing as
# merchant_embedding. The FTS5 table `search_fts` is SQLite-only, so it lives in
# migration 0026, not here; the triggers there delete a vector with its
# document, and `fa forget-embeddings` wipes them all.
text_embedding = Table(
    "text_embedding",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("doc_id", BigInteger, primary_key=True, autoincrement=False),
    Column("model", String(64), nullable=False),
    Column("dims", Integer, nullable=False),
    Column("vector", LargeBinary, nullable=False),
)

# Proposals (#138): the Assistant's only write. `payload` (JSON) is held only
# while pending; deciding clears it to "{}", so a rejection leaves no trace of
# what was proposed and a confirmed one's effect lives only where it was
# written (`result_id`: the Memory, Correction or note id).
proposal = Table(
    "proposal",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("payload", Text, nullable=False),
    Column("status", String(16), nullable=False, server_default="pending"),
    Column("result_id", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_at", DateTime(timezone=True)),
    CheckConstraint(text("kind IN ('memory', 'correction')"), name="ck_proposal_kind"),
    CheckConstraint(
        text("status IN ('pending', 'confirmed', 'rejected')"),
        name="ck_proposal_status",
    ),
    Index("ix_proposal_user_status", "user_id", "status"),
)

# A Memory exists only through a confirmed Proposal (`proposal_id`, unique) and
# carries its provenance: the turn, the user's own words and the date. Deleting
# one is a hard delete of the row and its Proposal (`secure_delete` zeroes them).
memory = Table(
    "memory",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "proposal_id", Integer, ForeignKey("proposal.id"), nullable=False, unique=True
    ),
    Column("type", String(16), nullable=False),
    Column("text", Text, nullable=False),
    Column("tags", Text, nullable=False),  # JSON list of short words
    Column("turn_id", String(64), nullable=False),
    Column("quote", Text, nullable=False),
    Column("said_on", Date, nullable=False),
    Column("review_on", Date),  # constraints never expire
    Column("created_at", DateTime(timezone=True), nullable=False),
    # goal and constraint only, in the user's own words (#257); never inferred
    Column("amount_minor", BigInteger),  # pence, > 0
    Column("due_on", Date),
    Column("priority", Integer),  # 1 (highest) to 5
    Column(  # goals only
        "linked_account_id",
        Integer,
        ForeignKey("account.id", ondelete="SET NULL", name="fk_memory_linked_account"),
    ),
    CheckConstraint(
        text("type IN ('preference', 'goal', 'constraint')"), name="ck_memory_type"
    ),
    CheckConstraint(text("length(text) BETWEEN 1 AND 280"), name="ck_memory_text"),
)

# The learning signal (kept; the user can wipe it). Today: rejected Proposals,
# by kind only, never what they said.
feedback = Table(
    "feedback",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("proposal_id", Integer, ForeignKey("proposal.id")),
    Column("kind", String(16), nullable=False),
    Column("signal", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Assistant graph checkpoints (#139, ADR 0008): LangGraph's saved state per
# conversation thread, in this encrypted database only. They hold the session's
# messages and replies, so they live for the session: deleted when the user ends
# it and purged after a day idle (`secure_delete` zeroes the pages).
assistant_checkpoint = Table(
    "assistant_checkpoint",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("thread_id", String(64), primary_key=True),
    Column("checkpoint_ns", String(255), primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("parent_id", String(64)),
    Column("checkpoint_type", String(32), nullable=False),
    Column("checkpoint", LargeBinary, nullable=False),
    Column("metadata_type", String(32), nullable=False),
    Column("metadata", LargeBinary, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

assistant_checkpoint_write = Table(
    "assistant_checkpoint_write",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("thread_id", String(64), primary_key=True),
    Column("checkpoint_ns", String(255), primary_key=True),
    Column("checkpoint_id", String(64), primary_key=True),
    Column("task_id", String(64), primary_key=True),
    Column("idx", Integer, primary_key=True),
    Column("channel", String(255), nullable=False),
    Column("value_type", String(32), nullable=False),
    Column("value", LargeBinary, nullable=False),
    Column("task_path", String(255), nullable=False),
)

# The answer cache (#140, ADR 0010): a grounded reply keyed by a hash of
# (intent, tool calls with arguments, Ledger version, day). Derived, never the
# only copy: rows of an older Ledger version are dropped on the next store, and
# all of a user's go when a Memory changes.
answer_cache = Table(
    "answer_cache",
    metadata,
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("key", String(64), primary_key=True),
    Column("ledger_version", Integer, nullable=False),
    Column("reply", Text, nullable=False),
    Column("evidence", Text, nullable=False),  # JSON list of the reply's IDs
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# Insights (#150): the Engine's current findings, one per (type, key, day
# raised), rewritten by each refresh. Derived: a refresh deletes the ones no
# longer found. `evidence` is a JSON list of Transaction ids, `category` the
# "Top / Sub" label relevance is learned under.
insight = Table(
    "insight",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("type", String(32), nullable=False),
    Column("key", Text, nullable=False),  # the Merchant, or the Category label
    Column("category", Text, nullable=False),
    Column("raised_on", Date, nullable=False),
    Column("impact_minor", Integer, nullable=False),
    Column("confidence", Integer, nullable=False),  # percent
    Column("evidence", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        text(
            "type IN ('recurring', 'price_increase', 'category_spike',"
            " 'upcoming_renewal')"
        ),
        name="ck_insight_type",
    ),
    UniqueConstraint("user_id", "type", "key", "raised_on"),
)

# The user's explicit signal on an Insight (#150), one per Insight, the latest
# kept: "up", "down" or "dismissed". It carries the (type, Category) it was
# given under, so the relevance posterior keeps it after a refresh drops the
# Insight (`insight_id` is then NULL). The user can wipe it.
insight_feedback = Table(
    "insight_feedback",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("insight_id", Integer, ForeignKey("insight.id"), unique=True),
    Column("insight_type", String(32), nullable=False),
    Column("category", Text, nullable=False),
    Column("signal", String(16), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        text("signal IN ('up', 'down', 'dismissed')"),
        name="ck_insight_feedback_signal",
    ),
    Index("ix_insight_feedback_user", "user_id"),
)

# The user's regret-review answers (#152): "worth it?" on a discretionary
# Transaction, one per Transaction, with the week it was asked in. Feedback,
# never a Memory; the user can wipe it. It goes with its Transaction.
regret_answer = Table(
    "regret_answer",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "transaction_id",
        Integer,
        ForeignKey("transaction.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("week", Date, nullable=False),  # the Monday of the week asked
    Column("worth_it", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "transaction_id"),
)

# A confirmed Possible Duplicate (#158, ADR 0014): the synced Transaction the
# user dropped. An overlay like `correction` (ADR 0001), never deleted, only
# reverted: while live, the row counts in no total. Its own table because
# widening `ck_correction_kind` would rebuild `correction` (migration 0020).
duplicate_merge = Table(
    "duplicate_merge",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "transaction_id",
        Integer,
        ForeignKey("transaction.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("reverted_at", DateTime(timezone=True)),
    Index("ix_duplicate_merge_user", "user_id", "transaction_id"),
)

# "Not a Transfer" (#215 C): the user's word that a Transaction is no Transfer
# leg; while active, matching never pairs it. An overlay like a merge.
transfer_rejection = Table(
    "transfer_rejection",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "transaction_id",
        Integer,
        ForeignKey("transaction.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("reverted_at", DateTime(timezone=True)),
    Index("ix_transfer_rejection_user", "user_id", "transaction_id"),
)

# Subscription alerts already sent (#108), so each fires once per state:
# `state` is the flag's evidence date, or `converts_on` for a trial. Written
# insert-if-absent; never holds an amount.
subscription_alert = Table(
    "subscription_alert",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column(
        "merchant_id",
        Integer,
        ForeignKey("merchant.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("code", String(32), nullable=False),
    Column("state", Date, nullable=False),
    Column("alerted_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        text("code IN ('price_rise', 'charged_after_cancel', 'trial_converting')"),
        name="ck_subscription_alert_code",
    ),
    UniqueConstraint("user_id", "merchant_id", "code", "state"),
)

# The embedding model's guess for a Category Review item (#215 B), one per
# model version and Merchant, the live accuracy record: `decided_category_id`
# filled from the user's Merchant-wide Correction, `batch` (the ledger version
# its latest pass left; one sync is one batch) set when auto-applied,
# `undone_at` when the user undid that batch.
# Ids, scores and the version only: never a descriptor or an amount.
category_prediction = Table(
    "category_prediction",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("model", String(64), nullable=False),
    Column(
        "merchant_id",
        Integer,
        ForeignKey("merchant.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("category_id", Integer, ForeignKey("category.id"), nullable=False),
    Column("score", Integer, nullable=False),  # cosine, thousandths
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("decided_category_id", Integer, ForeignKey("category.id")),
    Column("batch", Integer),
    Column("undone_at", DateTime(timezone=True)),
    UniqueConstraint("user_id", "model", "merchant_id"),
)

# A model version the live accuracy monitor stopped (#215 B/G): it no longer
# auto-applies, and its earlier auto rows go back to the Review Queue.
model_suspension = Table(
    "model_suspension",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("model", String(64), nullable=False),
    Column("decisions", Integer, nullable=False),  # how many it was judged on
    Column("suspended_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("user_id", "model"),
)

# The Assistant's model per user (0024): "fast" or "careful"; no row is the
# default in code (assistant.ollama.DEFAULT_CHOICE).
assistant_setting = Table(
    "assistant_setting",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False, unique=True),
    Column("model", String(16), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        text("model IN ('fast', 'careful')"), name="ck_assistant_setting_model"
    ),
)

# The exposure log (#261, 0027, ADR 0015): every item shown to the user, once
# per week, with its slate slot, the policy and the exact probability it had of
# being shown (a Fraction, "num/den"), so later policies can be evaluated off
# policy. `item` is an Insight id, a card id or a route; `context` is JSON of
# ints and short codes only (for a route: features and cluster id, never the
# question). `outcome` comes only from an explicit action, never an open.
# Feedback, never a Memory: the user can wipe it, and rows go after 400 days.
exposure = Table(
    "exposure",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("item", String(64), nullable=False),
    Column("week", Date, nullable=False),  # its Monday
    Column("slot", Integer, nullable=False),
    Column("slate_size", Integer, nullable=False),
    Column("policy", String(64), nullable=False),
    Column("propensity", String(32), nullable=False),
    Column("context", Text, nullable=False),
    Column("shown_at", DateTime(timezone=True), nullable=False),
    Column("outcome", String(16)),
    Column("outcome_at", DateTime(timezone=True)),
    CheckConstraint(
        text("kind IN ('insight', 'card', 'model_route')"), name="ck_exposure_kind"
    ),
    CheckConstraint(
        text(
            "outcome IN ('accepted', 'rejected', 'dismissed', 'acted',"
            " 'regret_worth_it', 'regret_not_worth_it')"
        ),
        name="ck_exposure_outcome",
    ),
    UniqueConstraint("user_id", "kind", "item", "week"),
)
