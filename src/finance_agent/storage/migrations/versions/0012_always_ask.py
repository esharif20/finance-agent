"""merchant.always_ask: the user's "Always ask" flag on a Merchant, holding
how it was set ('user' or 'suggestion'); NULL is off (#125)

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("merchant", recreate="always") as batch:
        batch.add_column(sa.Column("always_ask", sa.String(16)))
        batch.create_check_constraint(
            "ck_merchant_always_ask", "always_ask IN ('user', 'suggestion')"
        )


def downgrade() -> None:
    with op.batch_alter_table("merchant", recreate="always") as batch:
        batch.drop_constraint("ck_merchant_always_ask", type_="check")
        batch.drop_column("always_ask")
