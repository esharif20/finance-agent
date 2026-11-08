"""The Assistant's answer cache (#140, ADR 0010): one new table in the
encrypted database, nothing rebuilt

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "answer_cache",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("ledger_version", sa.Integer, nullable=False),
        sa.Column("reply", sa.Text, nullable=False),
        sa.Column("evidence", sa.Text, nullable=False),  # JSON list of IDs
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("answer_cache")
