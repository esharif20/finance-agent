"""Merchant embeddings (#113): a new merchant_embedding table, deleted with its
Merchant (ON DELETE CASCADE) or with the last Transaction naming it (trigger),
and an index on categorisation.merchant_id for that trigger

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

# A new table only: nothing referenced is rebuilt (`open_db` migrates with
# foreign keys on). The trigger fires on any Transaction delete (an Import or
# Account deletion, a sync match): a deletion path must delete a Transaction's
# categorisation first (foreign key), so a Merchant with no categorisation left
# names no Transaction, and its vector goes. It errs towards deleting: vectors
# are derived and rebuilt by `embedding.refresh`. It fires once per deleted
# row, so it probes ix_categorisation_merchant once per stored vector rather
# than scanning categorisation (verifier #189: 10k deletes took ~50 s).
TRIGGER = """
CREATE TRIGGER merchant_embedding_source_gone AFTER DELETE ON "transaction"
BEGIN
    DELETE FROM merchant_embedding
    WHERE user_id = OLD.user_id
      AND NOT EXISTS (
          SELECT 1 FROM categorisation
          WHERE categorisation.merchant_id = merchant_embedding.merchant_id
      );
END
"""


def upgrade() -> None:
    op.create_table(
        "merchant_embedding",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "merchant_id",
            sa.Integer,
            sa.ForeignKey("merchant.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("dims", sa.Integer, nullable=False),
        sa.Column("vector", sa.LargeBinary, nullable=False),
    )
    op.create_index("ix_categorisation_merchant", "categorisation", ["merchant_id"])
    # shortcut: SQLite trigger syntax; a Postgres move (ADR 0003) needs a
    # trigger function instead.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS merchant_embedding_source_gone")
    op.drop_table("merchant_embedding")
    op.drop_index("ix_categorisation_merchant", "categorisation")
