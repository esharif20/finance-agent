""" "Not a Transfer" (#215 C): one new table, nothing rebuilt

The user's word that a row is no Transfer leg. Its own overlay table, like
`duplicate_merge` (0020): widening `ck_correction_kind` would copy
`correction`, which other tables reference.

Revision ID: 0022
Revises: 0021
"""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_rejection",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True)),
        sa.Index("ix_transfer_rejection_user", "user_id", "transaction_id"),
    )


def downgrade() -> None:
    op.drop_table("transfer_rejection")
