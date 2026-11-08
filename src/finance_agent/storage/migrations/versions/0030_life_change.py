"""Confirmed life changes (#270, migration 6 of #251): one new table

A change the user confirmed from the Review Queue: from `month` (YYYY-MM) a
series (income, rent, bills or groceries) is at a new level, so baselines and
forecasts read only its data from then. Only a confirmation writes a row; undo
deletes it, which restores the earlier baselines. `before_minor` and
`after_minor` are the detector's monthly medians in pence, kept for display.

Revision ID: 0030
Revises: 0029
"""

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "life_change",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("series", sa.String(16), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("before_minor", sa.BigInteger, nullable=False),
        sa.Column("after_minor", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "series", "month"),
    )


def downgrade() -> None:
    op.drop_table("life_change")
