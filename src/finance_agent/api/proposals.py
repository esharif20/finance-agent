"""Proposal and Memory endpoints (#138): list pending Proposals for the confirm
card, confirm (idempotent) or reject one, list and delete Memories, and wipe
feedback, for `current_user` only. There is no endpoint that creates a
Proposal: only the Assistant's tools do. Errors never quote Memory or note
text."""

from collections.abc import Callable, Iterator
from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.identity import current_user
from finance_agent.ledger import (
    AlwaysAskMerchant,
    InvalidSplit,
    KindHasNoCategory,
    Ledger,
    UnknownTarget,
)
from finance_agent.ledger.proposals import (
    AlreadyDecided,
    CategoryChange,
    CorrectionDraft,
    InvalidProposal,
    MemoryDraft,
    Proposal,
    Proposals,
    SplitChange,
)

SQLITE_MAX = 2**63 - 1
Id = Annotated[int, Path(ge=1, le=SQLITE_MAX)]


class MemoryCardOut(BaseModel):
    type: str
    text: str
    tags: list[str]
    quote: str  # the user's own words
    said_on: date


class PartOut(BaseModel):
    amount_minor: int
    category: list[str]


class CorrectionCardOut(BaseModel):
    transaction_id: int
    kind: Literal["category", "split", "note"]
    category: list[str] | None
    # a Split's parts as the Ledger would save them (the remainder filled in),
    # so every figure on the card comes from the Ledger, not the model
    parts: list[PartOut] | None
    note: str | None
    part: int | None


class ProposalOut(BaseModel):
    id: int
    kind: Literal["memory", "correction"]
    status: Literal["pending", "confirmed", "rejected"]
    created_at: datetime
    decided_at: datetime | None
    result_id: int | None
    memory: MemoryCardOut | None  # only while pending
    correction: CorrectionCardOut | None  # only while pending
    ledger_version: int


class ProposalsOut(BaseModel):
    items: list[ProposalOut]


class MemoryOut(BaseModel):
    id: int
    type: str
    text: str
    tags: list[str]
    turn_id: str
    quote: str
    said_on: date
    review_on: date | None
    created_at: datetime


class MemoriesOut(BaseModel):
    items: list[MemoryOut]


class DeletedOut(BaseModel):
    deleted: int


def _store(request: Request) -> Iterator[tuple[Connection, Ledger, Proposals]]:
    with request.app.state.engine.begin() as conn:
        yield conn, Ledger(conn, current_user()), Proposals(conn, current_user())


Store = Annotated[tuple[Connection, Ledger, Proposals], Depends(_store)]
router = APIRouter()


def _call[T](f: Callable[[], T]) -> T:
    try:
        return f()
    except UnknownTarget:
        raise HTTPException(404, "no such Proposal, Memory or target") from None
    except AlreadyDecided as e:
        raise HTTPException(409, str(e)) from None
    except AlwaysAskMerchant as e:
        raise HTTPException(409, str(e)) from None
    except (InvalidProposal, InvalidSplit, KindHasNoCategory) as e:
        raise HTTPException(422, str(e)) from None


def _card(led: Ledger, p: Proposal) -> ProposalOut:
    memory = correction = None
    d = p.draft
    if isinstance(d, MemoryDraft):
        memory = MemoryCardOut(
            type=d.type,
            text=d.text,
            tags=list(d.tags),
            quote=d.provenance.quote,
            said_on=d.provenance.said_on,
        )
    elif isinstance(d, CorrectionDraft):
        c = d.change
        parts = None
        if isinstance(c, SplitChange):
            try:
                made = led.preview_split(
                    d.transaction_id, [(q.amount_minor, q.category) for q in c.parts]
                )
                parts = [
                    PartOut(amount_minor=q.amount_minor, category=list(q.category))
                    for q in made
                ]
            except (UnknownTarget, InvalidSplit, KindHasNoCategory):
                parts = None  # no longer applies; confirming it will say why
        correction = CorrectionCardOut(
            transaction_id=d.transaction_id,
            kind=c.kind,
            category=list(c.category) if isinstance(c, CategoryChange) else None,
            parts=parts,
            note=c.text if c.kind == "note" else None,
            part=c.part if c.kind == "note" else None,
        )
    return ProposalOut(
        id=p.id,
        kind=p.kind,
        status=p.status,
        created_at=p.created_at,
        decided_at=p.decided_at,
        result_id=p.result_id,
        memory=memory,
        correction=correction,
        ledger_version=led.ledger_version(),
    )


@router.get("/api/proposals")
def pending(db: Store) -> ProposalsOut:
    """Pending Proposals, oldest first: the confirm cards to show."""
    _, led, props = db
    return ProposalsOut(items=[_card(led, p) for p in props.pending()])


@router.get("/api/proposals/{proposal_id}")
def read(db: Store, proposal_id: Id) -> ProposalOut:
    _, led, props = db
    return _card(led, _call(lambda: props.get(proposal_id)))


@router.post("/api/proposals/{proposal_id}/confirm")
def confirm(db: Store, proposal_id: Id) -> ProposalOut:
    """Apply it through the same Ledger paths as the UI; confirming twice
    writes once."""
    conn, led, props = db
    categorise.seed_taxonomy(conn, current_user())  # as /api/corrections
    return _card(led, _call(lambda: props.confirm(proposal_id)))


@router.post("/api/proposals/{proposal_id}/reject")
def reject(db: Store, proposal_id: Id) -> ProposalOut:
    """Nothing is written; the rejection is kept as feedback."""
    _, led, props = db
    return _card(led, _call(lambda: props.reject(proposal_id)))


@router.get("/api/memories")
def memories(db: Store) -> MemoriesOut:
    _, _, props = db
    return MemoriesOut(
        items=[
            MemoryOut(
                id=m.id,
                type=m.type,
                text=m.text,
                tags=list(m.tags),
                turn_id=m.turn_id,
                quote=m.quote,
                said_on=m.said_on,
                review_on=m.review_on,
                created_at=m.created_at,
            )
            for m in props.memories()
        ]
    )


@router.delete("/api/memories/{memory_id}")
def delete_memory(db: Store, memory_id: Id) -> DeletedOut:
    """Hard delete: the Memory and the Proposal it came from."""
    _, _, props = db
    _call(lambda: props.delete_memory(memory_id))
    return DeletedOut(deleted=1)


@router.delete("/api/feedback")
def forget_feedback(db: Store) -> DeletedOut:
    _, _, props = db
    return DeletedOut(deleted=props.forget_feedback())
