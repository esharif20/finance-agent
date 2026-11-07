"""The Splits endpoints (#123): preview, save (a re-split replaces), read and
revert a Split of one Transaction, for `current_user` only. Amounts are signed
integer minor units as the Bank has them; the last part's amount may be left
out and the Ledger computes it as the remainder (ADR 0007)."""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.identity import current_user
from finance_agent.ledger import (
    CURRENCY,
    SPLIT_PARTS,
    InvalidSplit,
    KindHasNoCategory,
    Ledger,
    Split,
    SplitPart,
    UnknownTarget,
)

SQLITE_MAX = 2**63 - 1  # a larger value would be a 500 from the driver, not a 422
TransactionId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]
# strict: 700.0, "700" and true are refused rather than coerced to an int
Minor = Annotated[int, Field(strict=True, ge=-SQLITE_MAX, le=SQLITE_MAX)]


class PartIn(BaseModel):
    amount_minor: Minor | None = None  # only the last part may leave it out
    category: list[str] = Field(min_length=2, max_length=2)  # [top, sub]


class SplitIn(BaseModel):
    parts: list[PartIn] = Field(
        min_length=SPLIT_PARTS.start, max_length=SPLIT_PARTS.stop - 1
    )


class PartOut(BaseModel):
    amount_minor: int
    category: list[str]


class PreviewOut(BaseModel):
    parts: list[PartOut]  # as they would be saved, the remainder filled in


class SplitOut(PreviewOut):
    id: int  # the split Correction's id
    transaction_id: int
    amount_minor: int  # the Transaction's amount now
    currency: str
    # the amount changed since: counted whole under the largest part
    needs_recheck: bool
    ledger_version: int


class RevertOut(BaseModel):
    id: int
    ledger_version: int


def _ledger(request: Request) -> Iterator[tuple[Connection, Ledger]]:
    with request.app.state.engine.begin() as conn:
        yield conn, Ledger(conn, current_user())


Led = Annotated[tuple[Connection, Ledger], Depends(_ledger)]
router = APIRouter(prefix="/api/splits")


def _parts(parts: tuple[SplitPart, ...]) -> list[PartOut]:
    return [
        PartOut(amount_minor=p.amount_minor, category=list(p.category)) for p in parts
    ]


def _split(led: Ledger, s: Split) -> SplitOut:
    return SplitOut(
        id=s.id,
        transaction_id=s.transaction_id,
        parts=_parts(s.parts),
        amount_minor=s.amount_minor,
        currency=CURRENCY,
        needs_recheck=s.needs_recheck,
        ledger_version=led.ledger_version(),
    )


def _call[T](f: Callable[[], T]) -> T:
    try:
        return f()
    except UnknownTarget:
        raise HTTPException(404, "no such Transaction, Category or Split") from None
    except (InvalidSplit, KindHasNoCategory) as e:
        raise HTTPException(422, str(e)) from None


def _in(body: SplitIn) -> list[tuple[int | None, tuple[str, str]]]:
    return [(p.amount_minor, (p.category[0], p.category[1])) for p in body.parts]


@router.post("/{transaction_id}/preview")
def preview(db: Led, transaction_id: TransactionId, body: SplitIn) -> PreviewOut:
    """The parts as they would be saved; nothing is written."""
    conn, led = db
    categorise.seed_taxonomy(conn, current_user())
    return PreviewOut(
        parts=_parts(_call(lambda: led.preview_split(transaction_id, _in(body))))
    )


@router.put("/{transaction_id}")
def save(db: Led, transaction_id: TransactionId, body: SplitIn) -> SplitOut:
    """Split the Transaction; a live Split is replaced and kept in the history."""
    conn, led = db
    categorise.seed_taxonomy(conn, current_user())
    return _split(led, _call(lambda: led.split_transaction(transaction_id, _in(body))))


@router.get("/{transaction_id}")
def read(db: Led, transaction_id: TransactionId) -> SplitOut:
    _, led = db
    s = led.split(transaction_id)
    if s is None:
        raise HTTPException(404, "no live Split")
    return _split(led, s)


@router.delete("/{transaction_id}")
def revert(db: Led, transaction_id: TransactionId) -> RevertOut:
    """Undo the live Split as a whole; it stays in the history, reverted."""
    _, led = db
    cid = _call(lambda: led.revert_split(transaction_id))
    return RevertOut(id=cid, ledger_version=led.ledger_version())
