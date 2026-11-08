"""Monthly stories (#266, migration 5 of #251): one new table and one trigger

A month's structured recap as the Engine built it, with the fingerprint of the
data it was built from and, once #276 words it, the wording. Figures are
always recomputed on read; the wording is reused only while the fingerprint is
unchanged. A deleted Transaction (an Import or Account deletion) deletes its
month's story, so the trigger reaches every deletion path.

Revision ID: 0029
Revises: 0028
"""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

# posted_on is stored as YYYY-MM-DD, so its first 7 characters are the month
TRIGGER = (
    'CREATE TRIGGER monthly_story_transaction_gone AFTER DELETE ON "transaction" '
    "BEGIN DELETE FROM monthly_story WHERE user_id = OLD.user_id "
    "AND month = substr(OLD.posted_on, 1, 7); END"
)


def upgrade() -> None:
    op.create_table(
        "monthly_story",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("month", sa.String(7), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("recap", sa.Text, nullable=False),
        sa.Column("wording", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS monthly_story_transaction_gone")
    op.drop_table("monthly_story")
