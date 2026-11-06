"""sync_status: last sync, last failure and bank consent date

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_status",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("last_success_ts", sa.BigInteger),
        sa.Column("last_failure_ts", sa.BigInteger),
        sa.Column("failure_class", sa.String(64)),
        sa.Column("failures_in_a_row", sa.Integer, nullable=False, server_default="0"),
        sa.Column("consent_renewed_on", sa.Date),
        sa.Column("notified", sa.String(64)),
    )


def downgrade() -> None:
    op.drop_table("sync_status")
