"""subscription_entry: the Subscription registry (#103)

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_entry",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "merchant_id", sa.Integer, sa.ForeignKey("merchant.id"), nullable=False
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(16)),
        sa.Column("group_name", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(16)),
        sa.Column("evidence_on", sa.Date, nullable=False),
        sa.Column("charge_count", sa.Integer),
        sa.Column("interval", sa.String(16)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_subscription_entry_merchant",
        "subscription_entry",
        ["user_id", "merchant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_subscription_entry_merchant", "subscription_entry")
    op.drop_table("subscription_entry")
