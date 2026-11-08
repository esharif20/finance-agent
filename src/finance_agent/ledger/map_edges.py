"""The personal map's nodes and stored edges (#275, migration 7 of #251),
scoped to one user. `engine.map` derives the obvious edges on read.

A row here is a `map_edge` Proposal the user confirmed; only
`Proposals.confirm` calls `add`. Both nodes are checked to be this user's on
propose and again on confirm. An edge goes with either node (migration 0031's
triggers), with `remove` and with `forget`; its emptied Proposal goes with it.

Nothing here logs; names and ids never leave the database but as data.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Connection

from finance_agent.ledger import Ledger, UnknownTarget
from finance_agent.ledger.taxonomy import TAXONOMY_VERSION
from finance_agent.storage import Scoped, schema

_E = schema.map_edge
type Kind = Literal["merchant", "category", "goal", "life_change"]
KINDS: tuple[Kind, ...] = ("merchant", "category", "goal", "life_change")


@dataclass(frozen=True, order=True)
class Node:
    kind: Kind
    id: int


type Pair = tuple[Node, Node]  # always in order, a < b


def pair(a: Node, b: Node) -> Pair:
    """The edge between `a` and `b`, in its one stored order."""
    if a == b:
        raise ValueError("an edge joins two different nodes")
    return (a, b) if a < b else (b, a)


@dataclass(frozen=True)
class StoredEdge:
    id: int
    a: Node
    b: Node
    proposal_id: int
    created_at: datetime


class MapEdges:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)
        self._led = Ledger(conn, user_id)

    # --- nodes --------------------------------------------------------------

    def categories(self) -> dict[int, tuple[str, str]]:
        """id -> (top, leaf) of this user's Categories in today's taxonomy."""
        c = schema.category
        rows = self._db.execute(
            self._db.select(c, c.c.id, c.c.parent_id, c.c.name).where(
                c.c.taxonomy_version == TAXONOMY_VERSION
            )
        ).all()
        tops = {r.id: r.name for r in rows if r.parent_id is None}
        return {r.id: (tops[r.parent_id], r.name) for r in rows if r.parent_id in tops}

    def merchants(self) -> dict[int, str]:
        return {m.id: m.name for m in self._led.merchants()}

    def goals(self) -> dict[int, tuple[str, ...]]:
        """goal Memory id -> its tags."""
        m = schema.memory
        rows = self._db.execute(
            self._db.select(m, m.c.id, m.c.tags).where(m.c.type == "goal")
        )
        return {r.id: tuple(json.loads(r.tags)) for r in rows}

    def life_changes(self) -> dict[int, str]:
        """life change id -> its series."""
        lc = schema.life_change
        rows = self._db.execute(self._db.select(lc, lc.c.id, lc.c.series))
        return {r.id: r.series for r in rows}

    def exists(self, node: Node) -> bool:
        match node.kind:
            case "merchant":
                return node.id in self.merchants()
            case "category":
                return node.id in self.categories()
            case "goal":
                return node.id in self.goals()
            case "life_change":
                return node.id in self.life_changes()

    def check(self, a: Node, b: Node) -> Pair:
        """The ordered pair, once both nodes are this user's; else
        UnknownTarget (another user's node reads as no node at all)."""
        p = pair(a, b)
        if not all(self.exists(n) for n in p):
            raise UnknownTarget("no such node")
        return p

    # --- stored edges -------------------------------------------------------

    def add(self, a: Node, b: Node, proposal_id: int) -> int:
        """Store a confirmed edge; returns its id. An edge already on the map
        is not stored twice: the first one's id comes back."""
        x, y = self.check(a, b)
        found = self.find(x, y)
        if found is not None:
            return found
        return self._db.insert(
            _E,
            proposal_id=proposal_id,
            a_kind=x.kind,
            a_id=x.id,
            b_kind=y.kind,
            b_id=y.id,
            created_at=datetime.now(UTC),
        )

    def find(self, a: Node, b: Node) -> int | None:
        """The stored edge between `a` and `b`, either way round."""
        x, y = pair(a, b)
        return self._db.execute(
            self._db.select(_E, _E.c.id).where(
                _E.c.a_kind == x.kind,
                _E.c.a_id == x.id,
                _E.c.b_kind == y.kind,
                _E.c.b_id == y.id,
            )
        ).scalar()

    def stored(self) -> list[StoredEdge]:
        rows = self._db.execute(self._db.select(_E).order_by(_E.c.id))
        return [
            StoredEdge(
                r.id,
                Node(r.a_kind, r.a_id),
                Node(r.b_kind, r.b_id),
                r.proposal_id,
                r.created_at,
            )
            for r in rows
        ]

    def remove(self, edge_id: int) -> bool:
        """Delete one confirmed edge (and its Proposal); False if gone."""
        res = self._db.execute(self._db.delete(_E).where(_E.c.id == edge_id))
        return res.rowcount > 0

    def forget(self) -> int:
        """Delete every confirmed edge of this user."""
        return self._db.execute(self._db.delete(_E)).rowcount
