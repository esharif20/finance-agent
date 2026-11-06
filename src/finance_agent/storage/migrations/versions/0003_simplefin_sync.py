"""simplefin sync: synced_key, sync_anchor, sync_gap, possible_duplicate;
nullable running balance

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
    # The batch rebuild drops and renames `transaction`; FKs are checked at commit.
    op.execute("PRAGMA defer_foreign_keys = ON")
    with op.batch_alter_table("transaction") as b:
        b.alter_column(
            "running_balance_minor", existing_type=sa.BigInteger, nullable=True
        )
        b.add_column(
            sa.Column("origin", sa.String(16), nullable=False, server_default="csv")
        )
    op.add_column("account", sa.Column("provider_ref", sa.String(64)))
    op.create_index(
        "ux_account_provider_ref", "account", ["user_id", "provider_ref"], unique=True
    )
    op.create_table(
        "synced_key",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column(
            "account_id", sa.Integer, sa.ForeignKey("account.id"), primary_key=True
        ),
        sa.Column("provider_id", sa.String(256), primary_key=True),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            nullable=False,
        ),
        sa.Column("posted_ts", sa.BigInteger, nullable=False),
        sa.Column("transacted_ts", sa.BigInteger),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("description", sa.String(256), nullable=False),
    )
    op.create_table(
        "sync_anchor",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "account_id", sa.Integer, sa.ForeignKey("account.id"), primary_key=True
        ),
        sa.Column("balance_minor", sa.BigInteger, nullable=False),
        sa.Column("balance_ts", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "sync_gap",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False
        ),
        sa.Column(
            "statement_import_id",
            sa.Integer,
            sa.ForeignKey("statement_import.id"),
            nullable=False,
        ),
        sa.Column("balance_ts", sa.BigInteger, nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
    )

    op.create_table(
        "possible_duplicate",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("group_id", sa.Integer, primary_key=True),
        sa.Column(
            "transaction_id",
            sa.Integer,
            sa.ForeignKey("transaction.id"),
            primary_key=True,
        ),
        sa.Column(
            "account_id", sa.Integer, sa.ForeignKey("account.id"), nullable=False
        ),
        sa.Column(
            "statement_import_id",
            sa.Integer,
            sa.ForeignKey("statement_import.id"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("possible_duplicate")
    op.drop_table("sync_gap")
    op.drop_table("sync_anchor")
    op.drop_table("synced_key")
    op.drop_index("ux_account_provider_ref", table_name="account")
    op.drop_column("account", "provider_ref")
    op.execute("PRAGMA defer_foreign_keys = ON")
    with op.batch_alter_table("transaction") as b:
        b.drop_column("origin")
        b.alter_column(
            "running_balance_minor", existing_type=sa.BigInteger, nullable=False
        )
