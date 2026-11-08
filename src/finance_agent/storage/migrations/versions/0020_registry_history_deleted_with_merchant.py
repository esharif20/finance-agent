"""Registry history goes with its Merchant (#132): a trigger, nothing rebuilt

Revision ID: 0020
Revises: 0019
"""

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

# subscription_entry is append-only, so its rows are only ever removed here.
# A trigger rather than ON DELETE CASCADE: that would rebuild the table, and
# `open_db` migrates with foreign keys on. BEFORE DELETE, so the children are
# gone when the foreign key is checked.
TRIGGER = """
CREATE TRIGGER subscription_entry_merchant_gone BEFORE DELETE ON merchant
BEGIN
    DELETE FROM subscription_entry WHERE merchant_id = OLD.id;
END
"""


def upgrade() -> None:
    op.execute(TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER subscription_entry_merchant_gone")
