"""Personal map edges (#275, migration 7 of #251): one new table, a new
Proposal kind

The obvious edges of the personal map (Merchant to Category, goal to its
tagged Categories, life change to its series) are derived on read, so they are
never stored. A `map_edge` row is one the Assistant proposed and the user
confirmed, between two of the user's nodes: a Merchant, a Category, a goal
Memory or a confirmed life change. Triggers delete an edge with either node,
and an edge's (already emptied) Proposal with the edge.

Revision ID: 0031
Revises: 0030
"""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

KINDS = "('merchant', 'category', 'goal', 'life_change')"
NODES = {  # node kind -> the table its id points at
    "merchant": "merchant",
    "category": "category",
    "goal": "memory",
    "life_change": "life_change",
}


def _node_gone(kind: str, table: str) -> str:
    # constant names from NODES, never input
    return f"""
CREATE TRIGGER map_edge_{kind}_gone AFTER DELETE ON {table}
BEGIN
    DELETE FROM map_edge
    WHERE user_id = OLD.user_id
      AND ((a_kind = '{kind}' AND a_id = OLD.id)
           OR (b_kind = '{kind}' AND b_id = OLD.id));
END
"""  # noqa: S608


EDGE_GONE = """
CREATE TRIGGER map_edge_gone AFTER DELETE ON map_edge
BEGIN
    DELETE FROM proposal WHERE id = OLD.proposal_id AND user_id = OLD.user_id;
END
"""


def _kinds(*kinds: str) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    # the batch rebuild drops and renames `proposal`; FKs are checked at commit
    op.execute("PRAGMA defer_foreign_keys = ON")
    with op.batch_alter_table("proposal", recreate="always") as b:
        b.drop_constraint("ck_proposal_kind", type_="check")
        b.create_check_constraint(
            "ck_proposal_kind", _kinds("memory", "correction", "map_edge")
        )
    op.create_table(
        "map_edge",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("user.id"), nullable=False),
        sa.Column(
            "proposal_id",
            sa.Integer,
            sa.ForeignKey("proposal.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("a_kind", sa.String(16), nullable=False),
        sa.Column("a_id", sa.Integer, nullable=False),
        sa.Column("b_kind", sa.String(16), nullable=False),
        sa.Column("b_id", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"a_kind IN {KINDS}", name="ck_map_edge_a_kind"),
        sa.CheckConstraint(f"b_kind IN {KINDS}", name="ck_map_edge_b_kind"),
        sa.UniqueConstraint("user_id", "a_kind", "a_id", "b_kind", "b_id"),
    )
    if op.get_bind().dialect.name == "sqlite":
        for kind, table in NODES.items():
            op.execute(_node_gone(kind, table))
        op.execute(EDGE_GONE)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS map_edge_gone")
        for kind in NODES:
            op.execute(f"DROP TRIGGER IF EXISTS map_edge_{kind}_gone")
    op.drop_table("map_edge")
    op.execute("DELETE FROM proposal WHERE kind = 'map_edge'")
    op.execute("PRAGMA defer_foreign_keys = ON")
    with op.batch_alter_table("proposal", recreate="always") as b:
        b.drop_constraint("ck_proposal_kind", type_="check")
        b.create_check_constraint("ck_proposal_kind", _kinds("memory", "correction"))
