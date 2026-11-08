"""The Assistant's answer cache (#140, ADR 0010), in the encrypted database.

A reply is found only by an exact key: the intent (the routed sub-graph), the
normalised question (#206: so a reply written for one question, or steered by
injected text, is never served for another that makes the same calls), the
tool calls with their arguments, the Ledger version and the day (so "last
month" is never read against another today). Any import or Correction bumps
the Ledger version, so a stale answer can never be served. Nothing here
compares wording: text similarity never returns an answer. Only the key's hash
is stored, never the question.

shortcut: the intent is the routed sub-graph; ADR 0010's wording -> intent
mapping by embedding comes with the #143 benchmark if it earns its place."""

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, NamedTuple

from sqlalchemy import Connection

from finance_agent.storage import Scoped, schema

_T = schema.answer_cache


class Hit(NamedTuple):
    """A cached reply and the evidence IDs its figures came from (ADR 0007)."""

    reply: str
    evidence: tuple[str, ...]


def normalise(question: str) -> str:
    """Case and runs of whitespace don't change what was asked."""
    return " ".join(question.casefold().split())


def cache_key(
    intent: str,
    question: str,
    calls: Sequence[dict[str, Any]],
    ledger_version: int,
    day: date,
) -> str:
    """The hash of the canonical JSON of the whole key; any change in any part
    gives another key."""
    body = json.dumps(
        [intent, normalise(question), list(calls), ledger_version, day.isoformat()],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


class AnswerCache:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def _key(
        self, intent: str, question: str, calls: Sequence[dict[str, Any]], day: date
    ) -> str:
        return cache_key(intent, question, calls, self._db.ledger_version(), day)

    def get(
        self, intent: str, question: str, calls: Sequence[dict[str, Any]], day: date
    ) -> Hit | None:
        version = self._db.ledger_version()
        row = self._db.execute(
            self._db.select(_T, _T.c.reply, _T.c.evidence).where(
                _T.c.key == self._key(intent, question, calls, day),
                _T.c.ledger_version == version,
            )
        ).first()
        if row is None:
            return None
        return Hit(str(row.reply), tuple(json.loads(row.evidence)))

    def put(
        self,
        intent: str,
        question: str,
        calls: Sequence[dict[str, Any]],
        day: date,
        reply: str,
        evidence: Sequence[str],
    ) -> None:
        version = self._db.ledger_version()
        key = self._key(intent, question, calls, day)
        self._db.execute(  # stale rows, and this key's old row
            self._db.delete(_T).where(
                (_T.c.ledger_version != version) | (_T.c.key == key)
            )
        )
        self._db.insert(
            _T,
            key=key,
            ledger_version=version,
            reply=reply,
            evidence=json.dumps(list(evidence)),
            created_at=datetime.now(UTC),
        )

    def clear(self) -> int:
        """Drop every cached answer of this user (a Memory changed)."""
        return self._db.execute(self._db.delete(_T)).rowcount
