"""Pairwise answers (#262, migration 4 of #251): one new table, nothing rebuilt

"Which would you cut first?" and "which matters more?": each row is a pair
asked, then answered once. A side is a Category, a goal Memory or a card id,
and the row goes with its Category or Memory. Feedback, never a Memory.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sides = [
        col
        for s in ("a", "b")
        for col in (
            sa.Column(
                f"{s}_category_id",
                sa.Integer,
                sa.ForeignKey("category.id", ondelete="CASCADE"),
            ),
            sa.Column(
                f"{s}_memory_id",
                sa.Integer,
                sa.ForeignKey("memory.id", ondelete="CASCADE"),
            ),
            sa.Column(f"{s}_card", sa.String(64)),
        )
    ]
    one_item = [
        sa.CheckConstraint(
            f"(({s}_category_id IS NOT NULL) + ({s}_memory_id IS NOT NULL)"
            f" + ({s}_card IS NOT NULL)) = 1",
            name=f"ck_pairwise_answer_{s}_one_item",
        )
        for s in ("a", "b")
    ]
    op.create_table(
        "pairwise_answer",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("question", sa.String(16), nullable=False),
        *sides,
        sa.Column("a_won", sa.Boolean),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "question IN ('cut', 'matters')", name="ck_pairwise_answer_question"
        ),
        *one_item,
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_pairwise_answer_user", "pairwise_answer", ["user_id", "question"]
    )


def downgrade() -> None:
    op.drop_index("ix_pairwise_answer_user", "pairwise_answer")
    op.drop_table("pairwise_answer")
