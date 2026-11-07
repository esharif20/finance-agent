"""initial: user, bank, account, statement_import, transaction, transaction_source

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "bank",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.UniqueConstraint("user_id", "name"),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("bank_id", sa.Integer, sa.ForeignKey("bank.id"), nullable=False),
        sa.Column("external_ref", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("masked_number", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.UniqueConstraint("user_id", "external_ref"),
    )
    op.create_table(
        "statement_import",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("original_bytes", sa.LargeBinary, nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "file_hash"),
    )
    op.create_table(
        "transaction",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False
        ),
        sa.Column("posted_on", sa.Date, nullable=False),
        sa.Column("bank_type", sa.String(16), nullable=False),
        sa.Column("description", sa.String(256), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("running_balance_minor", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
    )
    op.create_table(
        "transaction_source",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            primary_key=True,
        ),
        sa.Column(
            "statement_import_id",
            sa.Integer,
            sa.ForeignKey("statement_import.id"),
            primary_key=True,
        ),
        sa.Column("row_number", sa.Integer, primary_key=True),
    )


def downgrade() -> None:
    for name in (
        "transaction_source",
        "transaction",
        "statement_import",
        "account",
        "bank",
        "user",
    ):
        op.drop_table(name)
