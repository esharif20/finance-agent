"""Merchant embeddings (#113): a new merchant_embedding table, deleted with its
Merchant (ON DELETE CASCADE) or with the last Transaction naming it (trigger)

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
# are derived and rebuilt by `embedding.refresh`.
TRIGGER = """
CREATE TRIGGER merchant_embedding_source_gone AFTER DELETE ON "transaction"
BEGIN
    DELETE FROM merchant_embedding
    WHERE user_id = OLD.user_id
      AND merchant_id NOT IN (
          SELECT merchant_id FROM categorisation
          WHERE user_id = OLD.user_id AND merchant_id IS NOT NULL
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
    # shortcut: SQLite trigger syntax; a Postgres move (ADR 0003) needs a
    # trigger function instead.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS merchant_embedding_source_gone")
    op.drop_table("merchant_embedding")
