"""Monthly story storage (#266, decision 8 of #251), scoped to one user.

One row a month: the structured recap the Engine built (JSON, figures in
pence), the fingerprint of the data it was built from, and its wording once
#276 writes one. Nothing here builds or checks a recap: `engine.story` does,
and recomputes the figures on every read. A month's row goes with any of its
Transactions (trigger, migration 0029), so with an Import or Account deletion
touching that month, and all at once by `forget`.

Nothing here logs; figures and wording never leave the database.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection

from finance_agent.storage import Scoped, schema

_S = schema.monthly_story
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class StoredStory:
    month: str  # YYYY-MM
    fingerprint: str
    recap: dict[str, Any]  # the recap's JSON, as stored
    wording: str | None


def _check(month: str) -> str:
    if not _MONTH.fullmatch(month):
        raise ValueError("not a month (YYYY-MM)")
    return month


class Stories:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def put(
        self,
        month: str,
        fingerprint: str,
        recap: dict[str, Any],
        wording: str | None = None,
    ) -> None:
        """Store `month`'s story, replacing any earlier one."""
        self._db.execute(self._db.delete(_S).where(_S.c.month == _check(month)))
        self._db.insert(
            _S,
            month=month,
            fingerprint=fingerprint,
            recap=json.dumps(recap, sort_keys=True, separators=(",", ":")),
            wording=wording,
            created_at=datetime.now(UTC),
        )

    def get(self, month: str) -> StoredStory | None:
        row = self._db.execute(
            self._db.select(_S).where(_S.c.month == _check(month))
        ).first()
        if row is None:
            return None
        return StoredStory(
            row.month, row.fingerprint, json.loads(row.recap), row.wording
        )

    def months(self) -> list[str]:
        """The months with a stored story, oldest first."""
        rows = self._db.execute(self._db.select(_S, _S.c.month).order_by(_S.c.month))
        return [r.month for r in rows]

    def forget(self) -> int:
        """Delete every stored story of this user."""
        return self._db.execute(self._db.delete(_S)).rowcount
