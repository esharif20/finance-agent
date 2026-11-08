"""Assistant graph checkpoints (#139, ADR 0008): two new tables in the
encrypted database, nothing rebuilt

Revision ID: 0016
Revises: 0015
"""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_checkpoint",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(255), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("parent_id", sa.String(64)),
        sa.Column("checkpoint_type", sa.String(32), nullable=False),
        sa.Column("checkpoint", sa.LargeBinary, nullable=False),
        sa.Column("metadata_type", sa.String(32), nullable=False),
        sa.Column("metadata", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "assistant_checkpoint_write",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(255), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), primary_key=True),
        sa.Column("idx", sa.Integer, primary_key=True),
        sa.Column("channel", sa.String(255), nullable=False),
        sa.Column("value_type", sa.String(32), nullable=False),
        sa.Column("value", sa.LargeBinary, nullable=False),
        sa.Column("task_path", sa.String(255), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("assistant_checkpoint_write")
    op.drop_table("assistant_checkpoint")
