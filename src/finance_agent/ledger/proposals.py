"""Proposals and Memories (#138, docs/AI_DESIGN.md "Memory"): the Assistant's
only write path. A Proposal is a Memory or a Correction (a Category, a Split or
a purpose note) and does nothing until the user confirms it.

- Proposing validates by running the confirm path inside a savepoint that is
  always rolled back, so a Proposal that would fail on confirm (another
  user's Transaction, an unknown Category, a bad Split) is refused up front
  and nothing outside the `proposal` row is ever written.
- Confirming goes through the same Ledger methods as the UI Corrections and is
  idempotent: the Proposal is claimed (pending -> confirmed) before the write,
  so a second confirm returns the first result and writes nothing.
- Rejecting is recorded as feedback (the kind only). Deciding either way clears
  the payload, so a rejected Proposal leaves no trace of what it said.

Memory text, quotes and note text are never logged and never quoted in errors.
"""

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from sqlalchemy import Connection

from finance_agent.ledger import (
    NOTE_MAX,
    SPLIT_PARTS,
    InvalidNote,
    Ledger,
    UnknownTarget,
)
from finance_agent.ledger.answer_cache import AnswerCache
from finance_agent.storage import Scoped, schema

MEMORY_MAX = 280  # characters
QUOTE_MAX = 500
REVIEW_AFTER = timedelta(days=182)  # preferences and goals are re-asked (DATA_POLICY)
MemoryType = Literal["preference", "goal", "constraint"]
_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)
Tag = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,23}$")]
CategoryName = Annotated[str, StringConstraints(min_length=1, max_length=40)]
Minor = Annotated[int, Field(ge=-(2**63 - 1), le=2**63 - 1)]
Pence = Annotated[int, Field(gt=0, le=2**63 - 1)]
Priority = Annotated[int, Field(ge=1, le=5)]  # 1 is the highest
GOAL_FIELDS = ("amount_minor", "due_on", "priority", "linked_account_id")


class InvalidProposal(ValueError):
    """The Proposal is malformed. The message never quotes its text."""


class AlreadyDecided(ValueError):
    """Confirming a rejected Proposal, or rejecting a confirmed one."""


class Provenance(BaseModel):
    """Where the user said it: the turn, their own words and the date."""

    model_config = _STRICT
    turn_id: str = Field(min_length=1, max_length=64)
    quote: str = Field(min_length=1, max_length=QUOTE_MAX)
    said_on: date


class MemoryDraft(BaseModel):
    model_config = _STRICT
    type: MemoryType
    text: str = Field(min_length=1, max_length=MEMORY_MAX)
    tags: tuple[Tag, ...] = Field(default=(), max_length=5)
    provenance: Provenance
    # goal and constraint only (#257), from the user's words, never inferred;
    # `Proposals` checks which type may carry which, and the Account's owner
    amount_minor: Pence | None = None
    due_on: date | None = None
    priority: Priority | None = None
    linked_account_id: int | None = Field(default=None, ge=1, le=2**63 - 1)


class CategoryChange(BaseModel):
    model_config = _STRICT
    kind: Literal["category"] = "category"
    category: tuple[CategoryName, CategoryName]


class SplitPartIn(BaseModel):
    model_config = _STRICT
    amount_minor: Minor | None = None  # the last part's may be left to the Ledger
    category: tuple[CategoryName, CategoryName]


class SplitChange(BaseModel):
    model_config = _STRICT
    kind: Literal["split"] = "split"
    parts: tuple[SplitPartIn, ...] = Field(
        min_length=SPLIT_PARTS.start, max_length=SPLIT_PARTS.stop - 1
    )


class NoteChange(BaseModel):
    model_config = _STRICT
    kind: Literal["note"] = "note"
    text: str = Field(max_length=NOTE_MAX)  # the Ledger checks the rest
    part: int | None = Field(default=None, ge=0, le=9)


type Change = CategoryChange | SplitChange | NoteChange


class CorrectionDraft(BaseModel):
    model_config = _STRICT
    transaction_id: int = Field(ge=1, le=2**63 - 1)
    change: Annotated[Change, Field(discriminator="kind")]


_DRAFT = TypeAdapter(MemoryDraft | CorrectionDraft)


@dataclass(frozen=True)
class Proposal:
    id: int
    kind: Literal["memory", "correction"]
    status: Literal["pending", "confirmed", "rejected"]
    created_at: datetime
    decided_at: datetime | None
    result_id: int | None  # the Memory, Correction or note written on confirm
    draft: MemoryDraft | CorrectionDraft | None  # only while pending


@dataclass(frozen=True)
class Memory:
    id: int
    type: MemoryType
    text: str
    tags: tuple[str, ...]
    turn_id: str
    quote: str
    said_on: date
    review_on: date | None
    created_at: datetime
    amount_minor: int | None = None
    due_on: date | None = None
    priority: int | None = None
    linked_account_id: int | None = None


def _check_text(text: str, what: str) -> None:
    if not text.strip() or any(ord(c) < 32 or 0x7F <= ord(c) < 0xA0 for c in text):
        raise InvalidProposal(f"{what} cannot be empty or hold control characters")


class Proposals:
    """One user's Proposals and Memories, on the Ledger's connection."""

    def __init__(self, conn: Connection, user_id: int) -> None:
        self._conn = conn
        self._db = Scoped(conn, user_id)
        self._led = Ledger(conn, user_id)
        self._cache = AnswerCache(conn, user_id)

    # --- proposing ----------------------------------------------------------

    def propose_memory(self, draft: MemoryDraft) -> Proposal:
        _check_text(draft.text, "a Memory")
        _check_text(draft.provenance.quote, "a quote")
        self._check_goal_fields(draft)
        return self._insert("memory", draft)

    def _check_goal_fields(self, draft: MemoryDraft) -> None:
        """Run on propose and again on confirm: the date or the Account may
        have changed in between."""
        given = {f for f in GOAL_FIELDS if getattr(draft, f) is not None}
        if draft.type == "preference" and given:
            raise InvalidProposal("a preference has no amount, date or priority")
        if draft.type != "goal" and "linked_account_id" in given:
            raise InvalidProposal("only a goal is linked to an Account")
        if draft.due_on is not None and draft.due_on <= datetime.now(UTC).date():
            raise InvalidProposal("the due date has to be in the future")
        if draft.linked_account_id is not None:
            a = schema.account
            owned = self._db.execute(
                self._db.select(a, a.c.id).where(a.c.id == draft.linked_account_id)
            ).first()
            if owned is None:
                raise UnknownTarget("no such Account")

    def propose_correction(self, draft: CorrectionDraft) -> Proposal:
        """Refused (UnknownTarget, InvalidSplit, InvalidNote, ...) exactly when
        confirming it now would be; nothing is written but the Proposal."""
        sp = self._conn.begin_nested()
        try:
            self._apply(draft)
        finally:
            sp.rollback()
        return self._insert("correction", draft)

    def _insert(self, kind: str, draft: MemoryDraft | CorrectionDraft) -> Proposal:
        pid = self._db.insert(
            schema.proposal,
            kind=kind,
            payload=draft.model_dump_json(),
            status="pending",
            created_at=datetime.now(UTC),
        )
        return self.get(pid)

    # --- reading ------------------------------------------------------------

    def get(self, proposal_id: int) -> Proposal:
        p = schema.proposal
        row = self._db.execute(self._db.select(p).where(p.c.id == proposal_id)).first()
        if row is None:
            raise UnknownTarget("no such Proposal")
        return self._proposal(row)

    def pending(self) -> list[Proposal]:
        p = schema.proposal
        rows = self._db.execute(
            self._db.select(p).where(p.c.status == "pending").order_by(p.c.id)
        )
        return [self._proposal(r) for r in rows]

    @staticmethod
    def _proposal(r: Any) -> Proposal:
        draft = _DRAFT.validate_json(r.payload) if r.status == "pending" else None
        return Proposal(
            r.id, r.kind, r.status, r.created_at, r.decided_at, r.result_id, draft
        )

    def memories(self) -> list[Memory]:
        """Every confirmed Memory, newest first."""
        m = schema.memory
        rows = self._db.execute(self._db.select(m).order_by(m.c.id.desc()))
        return [
            Memory(
                r.id,
                r.type,
                r.text,
                tuple(json.loads(r.tags)),
                r.turn_id,
                r.quote,
                r.said_on,
                r.review_on,
                r.created_at,
                *(getattr(r, f) for f in GOAL_FIELDS),
            )
            for r in rows
        ]

    # --- deciding -----------------------------------------------------------

    def confirm(self, proposal_id: int) -> Proposal:
        """Apply the Proposal through the Ledger; a second confirm is a no-op
        that returns the first result. A failed write leaves the Proposal
        pending: claim and write share one savepoint."""
        p = schema.proposal
        current = self.get(proposal_id)
        if current.status == "confirmed":
            return current
        if current.status == "rejected" or current.draft is None:
            raise AlreadyDecided("this Proposal was rejected")
        with self._conn.begin_nested():  # a failed write undoes the claim too
            claimed = self._db.execute(
                self._db.update(p)
                .where(p.c.id == proposal_id, p.c.status == "pending")
                .values(status="confirmed", decided_at=datetime.now(UTC), payload="{}")
            )
            if claimed.rowcount == 1:
                result = self._apply(current.draft, proposal_id)
                if isinstance(current.draft, MemoryDraft):
                    self._cache.clear()  # answers may have leant on Memories
                self._db.execute(
                    self._db.update(p)
                    .where(p.c.id == proposal_id)
                    .values(result_id=result)
                )
        if claimed.rowcount != 1:  # decided between the read and the claim
            return self.confirm(proposal_id)
        return self.get(proposal_id)

    def reject(self, proposal_id: int) -> Proposal:
        """Record the rejection as feedback; the payload is cleared. Rejecting
        twice is a no-op."""
        p = schema.proposal
        current = self.get(proposal_id)
        if current.status == "rejected":
            return current
        if current.status == "confirmed":
            raise AlreadyDecided("this Proposal was confirmed")
        now = datetime.now(UTC)
        claimed = self._db.execute(
            self._db.update(p)
            .where(p.c.id == proposal_id, p.c.status == "pending")
            .values(status="rejected", decided_at=now, payload="{}")
        )
        if claimed.rowcount != 1:  # decided between the read and the claim
            return self.reject(proposal_id)
        self._db.insert(
            schema.feedback,
            proposal_id=proposal_id,
            kind=current.kind,
            signal="rejected",
            created_at=now,
        )
        return self.get(proposal_id)

    def _apply(
        self, draft: MemoryDraft | CorrectionDraft, proposal_id: int | None = None
    ) -> int:
        if isinstance(draft, MemoryDraft):
            if proposal_id is None:
                raise InvalidProposal("a Memory is written only from a Proposal")
            self._check_goal_fields(draft)
            return self._db.insert(
                schema.memory,
                proposal_id=proposal_id,
                type=draft.type,
                text=draft.text,
                tags=json.dumps(list(draft.tags)),
                turn_id=draft.provenance.turn_id,
                quote=draft.provenance.quote,
                said_on=draft.provenance.said_on,
                review_on=None
                if draft.type == "constraint"
                else draft.provenance.said_on + REVIEW_AFTER,
                created_at=datetime.now(UTC),
                **{f: getattr(draft, f) for f in GOAL_FIELDS},
            )
        t, c = draft.transaction_id, draft.change
        match c:
            case CategoryChange():
                return self._led.correct_transaction(t, c.category).id
            case SplitChange():
                parts = [(q.amount_minor, q.category) for q in c.parts]
                return self._led.split_transaction(t, parts).id
            case NoteChange():
                try:
                    return self._led.set_note(t, c.text, c.part).id
                except InvalidNote as e:
                    raise InvalidProposal(str(e)) from None

    # --- deleting (DATA_POLICY: hard delete) --------------------------------

    def delete_memory(self, memory_id: int) -> None:
        """Delete the Memory and the Proposal it came from."""
        m, p = schema.memory, schema.proposal
        pid = self._db.execute(
            self._db.select(m, m.c.proposal_id).where(m.c.id == memory_id)
        ).scalar()
        if pid is None:
            raise UnknownTarget("no such Memory")
        self._db.execute(self._db.delete(m).where(m.c.id == memory_id))
        self._db.execute(self._db.delete(p).where(p.c.id == pid))
        self._cache.clear()  # DATA_POLICY: gone from caches too

    def forget_feedback(self) -> int:
        """Wipe the feedback, the rejected Proposals it points at and every
        Insight rating, dismissal (#150) and regret answer (#152)."""
        f, p = schema.feedback, schema.proposal
        n = self._db.execute(self._db.delete(f)).rowcount
        for t in (schema.insight_feedback, schema.regret_answer):
            n += self._db.execute(self._db.delete(t)).rowcount
        self._db.execute(self._db.delete(p).where(p.c.status == "rejected"))
        return n
