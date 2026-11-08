"""Exposure log (#261, migration 3 of #251): one new table, nothing rebuilt

Every shown Insight, card or model route, once per week, with its slot, policy
and propensity, and later its explicit outcome (ADR 0015). Feedback, never a
Memory; the user can wipe it.

Revision ID: 0027
Revises: 0026
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exposure",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("item", sa.String(64), nullable=False),
        sa.Column("week", sa.Date, nullable=False),
        sa.Column("slot", sa.Integer, nullable=False),
        sa.Column("slate_size", sa.Integer, nullable=False),
        sa.Column("policy", sa.String(64), nullable=False),
        sa.Column("propensity", sa.String(32), nullable=False),
        sa.Column("context", sa.Text, nullable=False),
        sa.Column("shown_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(16)),
        sa.Column("outcome_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind IN ('insight', 'card', 'model_route')", name="ck_exposure_kind"
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted', 'rejected', 'dismissed', 'acted',"
            " 'regret_worth_it', 'regret_not_worth_it')",
            name="ck_exposure_outcome",
        ),
        sa.UniqueConstraint("user_id", "kind", "item", "week"),
    )


def downgrade() -> None:
    op.drop_table("exposure")
