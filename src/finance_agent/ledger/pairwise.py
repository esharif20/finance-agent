"""Pairwise preference questions and answers (#262, spec #251), scoped to one
user: "which would you cut first?" over two Categories and "which matters
more?" over two playbook cards or goals. `engine.preferences` fits strengths
from them; nothing here fits or ranks.

An item is a key: `category:<id>`, `goal:<Memory id>` or `card:<card id>`. A
pair is stored when it is asked; only a pair asked and not yet answered can
be answered, once, so an answer to a pair never asked is refused. Asking a new
pair replaces the question's unanswered one (a skip records nothing). A row
goes with its Category or goal Memory (foreign keys), and `drop_cards` removes
those naming a card no longer in the playbook.

Feedback, never a Memory: the user can wipe it (`forget`). Nothing here logs.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import Connection

from finance_agent.ledger import UnknownTarget
from finance_agent.storage import Scoped, schema

Question = Literal["cut", "matters"]
_P = schema.pairwise_answer
_KEY = re.compile(r"(category|goal):[1-9][0-9]{0,18}|card:[a-z0-9-]{1,60}")
_COLUMN = {"category": "category_id", "goal": "memory_id", "card": "card"}


class NotAsked(UnknownTarget):
    """No such pair is waiting for an answer."""


@dataclass(frozen=True)
class Asked:
    id: int
    question: Question
    a: str
    b: str


def _side(side: str, key: str) -> dict[str, Any]:
    if not _KEY.fullmatch(key):
        raise ValueError("not an item key")
    kind, ref = key.split(":", 1)
    return {f"{side}_{_COLUMN[kind]}": ref if kind == "card" else int(ref)}


def _key(r: Any, side: str) -> str:
    if (c := getattr(r, f"{side}_category_id")) is not None:
        return f"category:{c}"
    if (m := getattr(r, f"{side}_memory_id")) is not None:
        return f"goal:{m}"
    return f"card:{getattr(r, f'{side}_card')}"


class Pairwise:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def ask(self, question: Question, a: str, b: str) -> Asked:
        """Store `question` over (a, b) as asked, replacing its unanswered
        pair. The caller checks both are the user's items."""
        if a == b:
            raise ValueError("a pair needs two items")
        values = _side("a", a) | _side("b", b)
        self._db.execute(
            self._db.delete(_P).where(_P.c.question == question, _P.c.a_won.is_(None))
        )
        pid = self._db.insert(
            _P, question=question, asked_at=datetime.now(UTC), **values
        )
        return Asked(pid, question, a, b)

    def answer(self, pair_id: int, choice: str) -> Asked:
        """Answer an asked pair once. For "cut" `choice` is the item cut first,
        so the other wins; for "matters" it is the one that matters more."""
        row = self._db.execute(
            self._db.select(_P).where(_P.c.id == pair_id, _P.c.a_won.is_(None))
        ).first()
        if row is None:
            raise NotAsked("no such pair is waiting for an answer")
        asked = Asked(row.id, row.question, _key(row, "a"), _key(row, "b"))
        if choice not in (asked.a, asked.b):
            raise ValueError("the choice is not one of the pair")
        a_won = (choice == asked.a) != (asked.question == "cut")
        self._db.execute(
            self._db.update(_P)
            .where(_P.c.id == row.id)
            .values(a_won=a_won, answered_at=datetime.now(UTC))
        )
        return asked

    def answers(self, question: Question) -> list[tuple[str, str]]:
        """(winner, loser) per answer to `question`, oldest first."""
        rows = self._db.execute(
            self._db.select(_P)
            .where(_P.c.question == question, _P.c.a_won.is_not(None))
            .order_by(_P.c.id)
        )
        out: list[tuple[str, str]] = []
        for r in rows:
            a, b = _key(r, "a"), _key(r, "b")
            out.append((a, b) if r.a_won else (b, a))
        return out

    def drop_cards(self, keep: set[str]) -> int:
        """Delete rows naming a card not in `keep` (card ids): the card left
        the playbook, so its answers go with it."""
        return self._db.execute(
            self._db.delete(_P).where(
                (_P.c.a_card.is_not(None) & _P.c.a_card.not_in(keep))
                | (_P.c.b_card.is_not(None) & _P.c.b_card.not_in(keep))
            )
        ).rowcount

    def forget(self) -> int:
        """Wipe every question and answer (DATA_POLICY: feedback is the user's
        to wipe); strengths are back to their prior."""
        return self._db.execute(self._db.delete(_P)).rowcount
