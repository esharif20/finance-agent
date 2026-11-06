"""transfer_link, category, categorisation

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_link",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "from_transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            primary_key=True,
        ),
        sa.Column(
            "to_transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            nullable=False,
            unique=True,
        ),
    )
    op.create_table(
        "category",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("parent_id", sa.Integer, sa.ForeignKey("category.id")),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("taxonomy_version", sa.Integer, nullable=False),
    )
    op.create_table(
        "categorisation",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("merchant", sa.String(64)),
        sa.Column("category_id", sa.Integer, sa.ForeignKey("category.id")),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("taxonomy_version", sa.Integer, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("categorisation")
    op.drop_table("category")
    op.drop_table("transfer_link")
