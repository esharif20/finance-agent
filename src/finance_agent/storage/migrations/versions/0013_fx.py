"""FX with provenance (#156): transaction.gbp_minor (the Bank's own GBP amount
on a foreign-currency Transaction) and fx_rate (rates the user enters)

Revision ID: 0013
Revises: 0012
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# `transaction` is changed in place, never rebuilt: notes, splits and links
# reference it, and `open_db` migrates with foreign keys on.


def upgrade() -> None:
    op.execute('ALTER TABLE "transaction" ADD COLUMN gbp_minor BIGINT')
    op.create_table(
        "fx_rate",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.String(32), nullable=False),
        sa.Column("rate_on", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "currency", "rate_on"),
    )


def downgrade() -> None:
    op.drop_table("fx_rate")
    op.execute('ALTER TABLE "transaction" DROP COLUMN gbp_minor')
