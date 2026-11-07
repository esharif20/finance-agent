"""merchant.always_ask: the user's "Always ask" flag on a Merchant, holding
how it was set ('user' or 'suggestion'); NULL is off (#125)

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# In place, not a batch rebuild: other tables reference `merchant`, and
# `open_db` migrates inside a transaction with foreign keys on, where dropping
# the old table fails. A column-level CHECK still lets SQLite drop the column.


def upgrade() -> None:
    op.execute(
        "ALTER TABLE merchant ADD COLUMN always_ask VARCHAR(16)"
        " CONSTRAINT ck_merchant_always_ask"
        " CHECK (always_ask IN ('user', 'suggestion'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE merchant DROP COLUMN always_ask")
