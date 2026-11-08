"""Insight storage (#150): the Engine's current Insights and the user's explicit
signals on them, scoped to one user.

`replace` is the only write of Insights and is idempotent: each is keyed by
(type, key, day raised), so a re-run updates in place and keeps its id, and
an Insight no longer found is deleted. Its signal outlives it, detached, so the
relevance posterior never forgets a rating. A dismissed Insight is never
listed again while it is still found.

Nothing here logs; Merchant names and amounts never leave the database.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import Connection

from finance_agent.ledger import UnknownTarget
from finance_agent.storage import Scoped, schema

Signal = Literal["up", "down", "dismissed"]
_I, _F = schema.insight, schema.insight_feedback


@dataclass(frozen=True)
class Found:
    """An Insight as the Engine found it."""

    type: str
    key: str  # the Merchant, or the Category label of a spike
    category: str  # "Top / Sub"
    raised_on: date
    impact: int  # pence
    confidence: int  # percent
    evidence: tuple[int, ...]  # Transaction ids, ascending


@dataclass(frozen=True)
class StoredInsight(Found):
    id: int = 0
    signal: Signal | None = None


class Insights:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def replace(self, found: Iterable[Found]) -> None:
        """Make the stored Insights exactly `found`."""
        cols = ("category", "impact_minor", "confidence", "evidence")
        stored = {
            (r["type"], r["key"], r["raised_on"]): (r["id"], tuple(r[c] for c in cols))
            for r in self._db.execute(self._db.select(_I)).mappings()
        }
        have = {k: iid for k, (iid, _) in stored.items()}
        keep: set[int] = set()
        for f in found:
            values: dict[str, Any] = {
                "category": f.category,
                "impact_minor": f.impact,
                "confidence": f.confidence,
                "evidence": json.dumps(list(f.evidence)),
            }
            iid = have.get(k := (f.type, f.key, f.raised_on))
            if iid is not None and stored.get(k, (0, ()))[1] == tuple(values.values()):
                keep.add(iid)  # unchanged: no write, so a read takes no write lock
            elif iid is None:
                iid = have[k] = self._db.insert(
                    _I,
                    type=f.type,
                    key=f.key,
                    raised_on=f.raised_on,
                    created_at=datetime.now(UTC),
                    **values,
                )
            elif iid not in keep:
                self._db.execute(
                    self._db.update(_I).where(_I.c.id == iid).values(**values)
                )
            keep.add(iid)
        gone = set(have.values()) - keep
        if gone:
            self._db.execute(
                self._db.update(_F)
                .where(_F.c.insight_id.in_(gone))
                .values(insight_id=None)
            )
            self._db.execute(self._db.delete(_I).where(_I.c.id.in_(gone)))

    def _rows(self, *where: Any) -> list[StoredInsight]:
        rows = self._db.execute(
            self._db.select(_I, _I, _F.c.signal)
            .outerjoin(_F, self._db.on(_F, _F.c.insight_id == _I.c.id))
            .where(*where)
            .order_by(_I.c.id)
        )
        return [
            StoredInsight(
                r.type,
                r.key,
                r.category,
                r.raised_on,
                r.impact_minor,
                r.confidence,
                tuple(json.loads(r.evidence)),
                r.id,
                r.signal,
            )
            for r in rows
        ]

    def current(self) -> list[StoredInsight]:
        """Every stored Insight not dismissed, oldest first."""
        return self._rows((_F.c.signal.is_(None)) | (_F.c.signal != "dismissed"))

    def get(self, insight_id: int) -> StoredInsight:
        found = self._rows(_I.c.id == insight_id)
        if not found:
            raise UnknownTarget("no such Insight")
        return found[0]

    def give(self, insight_id: int, signal: Signal) -> StoredInsight:
        """Record the user's signal on one of their Insights; a later one
        replaces it."""
        i = self.get(insight_id)
        self._db.execute(self._db.delete(_F).where(_F.c.insight_id == i.id))
        self._db.insert(
            _F,
            insight_id=i.id,
            insight_type=i.type,
            category=i.category,
            signal=signal,
            created_at=datetime.now(UTC),
        )
        return self.get(insight_id)

    def signals(self) -> list[tuple[str, str, bool]]:
        """(type, Category, liked) per signal: a thumbs-up is a like, a
        thumbs-down or a dismissal is not."""
        rows = self._db.execute(
            self._db.select(_F, _F.c.insight_type, _F.c.category, _F.c.signal)
        )
        return [(r.insight_type, r.category, r.signal == "up") for r in rows]

    def forget_feedback(self) -> int:
        """Wipe every signal (DATA_POLICY: feedback is the user's to wipe);
        relevance is back to Beta(1, 1) and dismissed Insights return."""
        return self._db.execute(self._db.delete(_F)).rowcount
