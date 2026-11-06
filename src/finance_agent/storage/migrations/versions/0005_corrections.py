"""correction, user.ledger_version

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("ledger_version", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "correction",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("transaction_id", sa.Integer, sa.ForeignKey("transaction.id")),
        sa.Column("merchant_id", sa.Integer, sa.ForeignKey("merchant.id")),
        sa.Column(
            "category_id", sa.Integer, sa.ForeignKey("category.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reverted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(transaction_id IS NULL) != (merchant_id IS NULL)",
            name="ck_correction_one_target",
        ),
    )


def downgrade() -> None:
    op.drop_table("correction")
    with op.batch_alter_table("user") as batch:
        batch.drop_column("ledger_version")
