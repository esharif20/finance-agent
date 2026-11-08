"""Goal and constraint fields on Memories (#257, decision 4 of #251): four
nullable columns on memory (rebuilt, for the Account foreign key)

The amount (pence), due date and priority the user stated, and for a goal the
Account it is saved in. A preference has none of them; `Proposals` enforces
that and the rest (amount > 0, due date ahead, the Account is the user's).

Revision ID: 0025
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_COLUMNS = ("amount_minor", "due_on", "priority", "linked_account_id")


def upgrade() -> None:
    # a batch rebuild: SQLite cannot ALTER in a foreign key, and an inline
    # REFERENCES does not reflect its ON DELETE
    with op.batch_alter_table("memory") as batch:
        batch.add_column(sa.Column("amount_minor", sa.BigInteger))
        batch.add_column(sa.Column("due_on", sa.Date))
        batch.add_column(sa.Column("priority", sa.Integer))
        batch.add_column(sa.Column("linked_account_id", sa.Integer))
        batch.create_foreign_key(
            "fk_memory_linked_account",
            "account",
            ["linked_account_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("memory") as batch:
        for c in reversed(_COLUMNS):
            batch.drop_column(c)
