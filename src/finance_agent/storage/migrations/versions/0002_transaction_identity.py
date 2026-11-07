"""transaction identity index: (account, date, description, amount, balance)

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_transaction_identity",
        "transaction",
        [
            "user_id",
            "account_id",
            "posted_on",
            "description",
            "amount_minor",
            "running_balance_minor",
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_transaction_identity", table_name="transaction")
