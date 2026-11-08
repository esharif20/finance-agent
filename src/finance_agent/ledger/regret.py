"""Regret-review answers (#152): "worth it?" on one of the user's Transactions,
scoped to one user. Feedback, never a Memory; the user can wipe it. Nothing
here logs."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Connection

from finance_agent.ledger import UnknownTarget
from finance_agent.storage import Scoped, schema

_R, _T = schema.regret_answer, schema.transaction


@dataclass(frozen=True)
class Answer:
    week: date  # the Monday of the week it was asked in
    worth_it: bool


class Regret:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def answers(self) -> dict[int, Answer]:
        """Transaction id -> its answer."""
        rows = self._db.execute(
            self._db.select(_R, _R.c.transaction_id, _R.c.week, _R.c.worth_it)
        )
        return {r.transaction_id: Answer(r.week, r.worth_it) for r in rows}

    def answer(self, transaction_id: int, week: date, worth_it: bool) -> None:
        """Record the answer on one of the user's Transactions; a later one
        replaces it."""
        owned = self._db.execute(
            self._db.select(_T, _T.c.id).where(_T.c.id == transaction_id)
        ).first()
        if owned is None:
            raise UnknownTarget("no such Transaction")
        self._db.execute(self._db.delete(_R).where(_R.c.transaction_id == owned.id))
        self._db.insert(
            _R,
            transaction_id=owned.id,
            week=week,
            worth_it=worth_it,
            created_at=datetime.now(UTC),
        )

    def forget(self) -> int:
        """Wipe every answer (DATA_POLICY: feedback is the user's to wipe)."""
        return self._db.execute(self._db.delete(_R)).rowcount
