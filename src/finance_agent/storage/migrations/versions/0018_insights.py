"""Insights and their feedback (#150): two new tables, nothing rebuilt

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

TYPES = "'recurring', 'price_increase', 'category_spike', 'upcoming_renewal'"


def upgrade() -> None:
    op.create_table(
        "insight",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("raised_on", sa.Date, nullable=False),
        sa.Column("impact_minor", sa.Integer, nullable=False),
        sa.Column("confidence", sa.Integer, nullable=False),
        sa.Column("evidence", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"type IN ({TYPES})", name="ck_insight_type"),
        sa.UniqueConstraint("user_id", "type", "key", "raised_on"),
    )
    op.create_table(
        "insight_feedback",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("insight_id", sa.Integer, sa.ForeignKey("insight.id"), unique=True),
        sa.Column("insight_type", sa.String(32), nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("signal", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "signal IN ('up', 'down', 'dismissed')", name="ck_insight_feedback_signal"
        ),
        sa.Index("ix_insight_feedback_user", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("insight_feedback")
    op.drop_table("insight")
