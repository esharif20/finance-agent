"""correction.kind and split_part: a Split divides one Transaction into parts
(#123); a split Correction has no Category of its own

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

KIND = (
    "(kind = 'category' AND category_id IS NOT NULL) OR "
    "(kind = 'split' AND category_id IS NULL AND transaction_id IS NOT NULL)"
)


def upgrade() -> None:
    # SQLite cannot relax NOT NULL in place: batch copies the table, keeping
    # its reflected ck_correction_one_target
    with op.batch_alter_table("correction", recreate="always") as batch:
        batch.add_column(
            sa.Column("kind", sa.String(16), nullable=False, server_default="category")
        )
        batch.alter_column("category_id", existing_type=sa.Integer, nullable=True)
        batch.create_check_constraint("ck_correction_kind", KIND)
    op.create_table(
        "split_part",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "correction_id",
            sa.Integer,
            sa.ForeignKey("correction.id"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer, primary_key=True),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column(
            "category_id", sa.Integer, sa.ForeignKey("category.id"), nullable=False
        ),
        sa.CheckConstraint("amount_minor != 0", name="ck_split_part_nonzero"),
        sa.CheckConstraint("ordinal BETWEEN 0 AND 9", name="ck_split_part_ordinal"),
    )


def downgrade() -> None:
    # a split has no single Category to fall back to, so splits are dropped
    op.drop_table("split_part")
    op.execute("DELETE FROM correction WHERE kind = 'split'")
    with op.batch_alter_table("correction", recreate="always") as batch:
        batch.drop_constraint("ck_correction_kind", type_="check")
        batch.alter_column("category_id", existing_type=sa.Integer, nullable=False)
        batch.drop_column("kind")
