"""Auto-apply for the embedding model and its live accuracy record (#215 B):
two new tables, nothing rebuilt

`category_prediction` logs each model version's guess per Category Review
item, the user's answer, and the ones it auto-applied; `model_suspension`
records a version the accuracy monitor stopped.

Revision ID: 0023
Revises: 0022
"""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "category_prediction",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column(
            "merchant_id",
            sa.Integer,
            sa.ForeignKey("merchant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "category_id", sa.Integer, sa.ForeignKey("category.id"), nullable=False
        ),
        sa.Column("score", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_category_id", sa.Integer, sa.ForeignKey("category.id")),
        sa.Column("batch", sa.Integer),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("user_id", "model", "merchant_id"),
    )
    op.create_table(
        "model_suspension",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("decisions", sa.Integer, nullable=False),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "model"),
    )


def downgrade() -> None:
    op.drop_table("model_suspension")
    op.drop_table("category_prediction")
