"""Possible Duplicate merges (#158) and subscription alerts (#108): two new
tables, nothing rebuilt

Widening `ck_correction_kind` would mean copying `correction`, which
`split_part` and `note` reference; with foreign keys on that copy breaks
(#170, #178). So a merge is its own overlay table (ADR 0014, #158 note).

Revision ID: 0020
Revises: 0019
"""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

CODES = "'price_rise', 'charged_after_cancel', 'trial_converting'"


def upgrade() -> None:
    op.create_table(
        "duplicate_merge",
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
        sa.Index("ix_duplicate_merge_user", "user_id", "transaction_id"),
    )
    op.create_table(
        "subscription_alert",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "merchant_id",
            sa.Integer,
            sa.ForeignKey("merchant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("state", sa.Date, nullable=False),
        sa.Column("alerted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"code IN ({CODES})", name="ck_subscription_alert_code"),
        sa.UniqueConstraint("user_id", "merchant_id", "code", "state"),
    )


def downgrade() -> None:
    op.drop_table("subscription_alert")
    op.drop_table("duplicate_merge")
