"""reconciliation_gap: Ledger vs Bank running balance breaks

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
        "reconciliation_gap",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False
        ),
        sa.Column("from_date", sa.Date, nullable=False),
        sa.Column("to_date", sa.Date, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
    )
    # rows imported before this get 0; Bank order then falls back to row_number
    op.add_column(
        "transaction_source",
        sa.Column("bank_position", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("transaction_source") as b:
        b.drop_column("bank_position")
    op.drop_table("reconciliation_gap")
