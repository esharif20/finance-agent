"""review_decision: the Review Queue's answers and skips (#110); undo for a
Subscription registry decision

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_decision",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("item_type", sa.String(32), nullable=False),
        sa.Column("item_key", sa.Text, nullable=False),
        sa.Column("item_version", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("effect_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_review_decision_user", "review_decision", ["user_id"])
    op.add_column(
        "subscription_entry", sa.Column("reverted_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("subscription_entry", "reverted_at")
    op.drop_index("ix_review_decision_user", "review_decision")
    op.drop_table("review_decision")
