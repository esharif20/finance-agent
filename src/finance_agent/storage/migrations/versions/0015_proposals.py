"""Proposals, Memories and feedback (#138): three new tables, nothing rebuilt

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "proposal",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result_id", sa.Integer),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('memory', 'correction')", name="ck_proposal_kind"),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'rejected')", name="ck_proposal_status"
        ),
    )
    op.create_index("ix_proposal_user_status", "proposal", ["user_id", "status"])
    op.create_table(
        "memory",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "proposal_id",
            sa.Integer,
            sa.ForeignKey("proposal.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("tags", sa.Text, nullable=False),
        sa.Column("turn_id", sa.String(64), nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("said_on", sa.Date, nullable=False),
        sa.Column("review_on", sa.Date),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "type IN ('preference', 'goal', 'constraint')", name="ck_memory_type"
        ),
        sa.CheckConstraint("length(text) BETWEEN 1 AND 280", name="ck_memory_text"),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("proposal_id", sa.Integer, sa.ForeignKey("proposal.id")),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("signal", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("feedback")
    op.drop_table("memory")
    op.drop_index("ix_proposal_user_status", table_name="proposal")
    op.drop_table("proposal")
