"""The purpose-note endpoints (#124): read, set (an edit keeps the history) and
purge the note on one Transaction, or on part `part` of its Split, for
`current_user` only. Note text is never logged, and a refusal never quotes it."""

from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent.identity import current_user
from finance_agent.ledger import InvalidNote, Ledger, Note, UnknownTarget

SQLITE_MAX = 2**63 - 1
TransactionId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]
Part = Annotated[int | None, Query(ge=0, le=9)]


class NoteIn(BaseModel):
    # length and characters are checked by the Ledger, so a refusal never
    # echoes the text the way a pydantic error would
    text: str = Field(strict=True)


class VersionOut(BaseModel):
    text: str
    created_at: datetime
    superseded_at: datetime | None


class NoteOut(BaseModel):
    transaction_id: int
    part: int | None
    text: str
    created_at: datetime
    history: list[VersionOut]  # every version, oldest first; the last is live
    ledger_version: int


class PurgeOut(BaseModel):
    purged: int  # versions deleted
    ledger_version: int


def _ledger(request: Request) -> Iterator[tuple[Connection, Ledger]]:
    with request.app.state.engine.begin() as conn:
        yield conn, Ledger(conn, current_user())


Led = Annotated[tuple[Connection, Ledger], Depends(_ledger)]
router = APIRouter(prefix="/api/notes")


def _call[T](f: Callable[[], T]) -> T:
    try:
        return f()
    except UnknownTarget:
        raise HTTPException(404, "no such Transaction, Split part or note") from None
    except InvalidNote as e:
        raise HTTPException(422, str(e)) from None


def _out(led: Ledger, history: list[Note]) -> NoteOut:
    live = history[-1]
    return NoteOut(
        transaction_id=live.transaction_id,
        part=live.part,
        text=live.text,
        created_at=live.created_at,
        history=[
            VersionOut(
                text=n.text, created_at=n.created_at, superseded_at=n.superseded_at
            )
            for n in history
        ],
        ledger_version=led.ledger_version(),
    )


@router.get("/{transaction_id}")
def read(db: Led, transaction_id: TransactionId, part: Part = None) -> NoteOut:
    _, led = db
    if led.note(transaction_id, part) is None:
        raise HTTPException(404, "no note")
    return _out(led, led.note_history(transaction_id, part))


@router.put("/{transaction_id}")
def save(
    db: Led, transaction_id: TransactionId, body: NoteIn, part: Part = None
) -> NoteOut:
    """Set the note; the previous version is kept, superseded."""
    _, led = db
    _call(lambda: led.set_note(transaction_id, body.text, part))
    return _out(led, led.note_history(transaction_id, part))


@router.delete("/{transaction_id}")
def purge(db: Led, transaction_id: TransactionId, part: Part = None) -> PurgeOut:
    """Delete every version of the note. Backups keep it until they age out."""
    _, led = db
    n = _call(lambda: led.delete_note(transaction_id, part))
    return PurgeOut(purged=n, ledger_version=led.ledger_version())
