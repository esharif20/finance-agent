"""Regret-review answers (#152): one new table, nothing rebuilt

Revision ID: 0019
Revises: 0018
"""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regret_answer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week", sa.Date, nullable=False),
        sa.Column("worth_it", sa.Boolean, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "transaction_id"),
    )


def downgrade() -> None:
    op.drop_table("regret_answer")
