"""The Assistant's model setting: one new table, nothing rebuilt

Per user, "fast" or "careful" (the Settings screen's Faster or More careful).
No row means the default, which lives in code, so changing the default needs
no migration.

Revision ID: 0024
Revises: 0023
"""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_setting",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False, unique=True
        ),
        sa.Column("model", sa.String(16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "model IN ('fast', 'careful')", name="ck_assistant_setting_model"
        ),
    )


def downgrade() -> None:
    op.drop_table("assistant_setting")
