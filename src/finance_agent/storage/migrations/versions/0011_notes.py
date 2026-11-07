"""note: versioned purpose notes on a Transaction or a Split part (#124)

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            nullable=False,
        ),
        sa.Column("part", sa.Integer),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(text) BETWEEN 1 AND 140", name="ck_note_length"),
        sa.CheckConstraint("part BETWEEN 0 AND 9", name="ck_note_part"),
    )
    op.create_index("ix_note_target", "note", ["user_id", "transaction_id"])


def downgrade() -> None:
    op.drop_index("ix_note_target", table_name="note")
    op.drop_table("note")
