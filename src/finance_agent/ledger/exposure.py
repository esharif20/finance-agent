"""The exposure log (#261, #228, ADR 0015), scoped to one user: what was shown,
where in its slate, under which policy and with what probability, and later
the user's explicit outcome on it. Off-policy evaluation (`engine.ope`) reads
it; nothing else learns from it yet.

An item is logged once per week (its first showing that week), so a re-listing
writes nothing. `context` holds ints, booleans and short codes only, never
text: a model route (#285) logs its features (quantised to ints) and its
embedding cluster id, never the question. An outcome comes only from an
explicit action (a rating, a dismissal, an answer), never an open or a click.

Feedback, never a Memory: the user can wipe it (`forget`), and rows older than
`RETENTION_DAYS` go on the next write. Nothing here logs.
"""

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction
from typing import Literal

from sqlalchemy import Connection

from finance_agent.storage import Scoped, schema

Kind = Literal["insight", "card", "model_route"]
Outcome = Literal[
    "accepted",
    "rejected",
    "dismissed",
    "acted",
    "regret_worth_it",
    "regret_not_worth_it",
]
type ContextValue = int | bool | str
RETENTION_DAYS = 400
_CODE = re.compile(r"[a-z0-9_:.-]{1,32}")  # an id or a code, never prose
_E = schema.exposure


def week_of(day: date) -> date:
    """The Monday of `day`'s week."""
    return day - timedelta(days=day.weekday())


@dataclass(frozen=True)
class Shown:
    kind: Kind
    item: str  # an Insight id, a card id or a route
    slot: int
    slate_size: int
    propensity: Fraction
    context: Mapping[str, ContextValue] = field(default_factory=dict[str, ContextValue])


@dataclass(frozen=True)
class Exposure(Shown):
    id: int = 0
    week: date = date.min
    policy: str = ""
    outcome: Outcome | None = None


def _check(s: Shown) -> None:
    if not 0 < s.propensity <= 1:
        raise ValueError("a propensity is in (0, 1]")
    if not 0 <= s.slot < s.slate_size:
        raise ValueError("the slot is outside its slate")
    if not _CODE.fullmatch(s.item):
        raise ValueError("an item is an id or a code")
    for k, v in s.context.items():
        ok = isinstance(v, int) or (type(v) is str and _CODE.fullmatch(v))
        if not (_CODE.fullmatch(k) and ok):
            raise ValueError("context holds ints and short codes only, never text")


class Exposures:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def log(self, day: date, policy: str, shown: Iterable[Shown]) -> None:
        """Log each item shown in `day`'s week, unless it already was."""
        week = week_of(day)
        shown = list(shown)
        for s in shown:
            _check(s)
        self._db.execute(
            self._db.delete(_E).where(_E.c.week < week - timedelta(RETENTION_DAYS))
        )
        have = {
            (r.kind, r.item)
            for r in self._db.execute(
                self._db.select(_E, _E.c.kind, _E.c.item).where(_E.c.week == week)
            )
        }
        now = datetime.now(UTC)
        for s in shown:
            if (s.kind, s.item) in have:
                continue
            have.add((s.kind, s.item))
            self._db.insert(
                _E,
                kind=s.kind,
                item=s.item,
                week=week,
                slot=s.slot,
                slate_size=s.slate_size,
                policy=policy,
                propensity=str(s.propensity),
                context=json.dumps(dict(s.context), sort_keys=True),
                shown_at=now,
            )

    def record(self, kind: Kind, item: str, outcome: Outcome) -> bool:
        """The user's explicit outcome on the item's latest exposure; a later
        one replaces it. False when it was never shown (nothing written)."""
        latest = self._db.execute(
            self._db.select(_E, _E.c.id)
            .where(_E.c.kind == kind, _E.c.item == item)
            .order_by(_E.c.week.desc())
            .limit(1)
        ).first()
        if latest is None:
            return False
        self._db.execute(
            self._db.update(_E)
            .where(_E.c.id == latest.id)
            .values(outcome=outcome, outcome_at=datetime.now(UTC))
        )
        return True

    def logged(self, kind: Kind | None = None) -> list[Exposure]:
        """Every exposure (of `kind`), oldest first."""
        q = self._db.select(_E).order_by(_E.c.week, _E.c.slot, _E.c.id)
        if kind is not None:
            q = q.where(_E.c.kind == kind)
        return [
            Exposure(
                r.kind,
                r.item,
                r.slot,
                r.slate_size,
                Fraction(r.propensity),
                json.loads(r.context),
                r.id,
                r.week,
                r.policy,
                r.outcome,
            )
            for r in self._db.execute(q)
        ]

    def forget(self) -> int:
        """Wipe the whole log (DATA_POLICY: feedback is the user's to wipe)."""
        return self._db.execute(self._db.delete(_E)).rowcount
