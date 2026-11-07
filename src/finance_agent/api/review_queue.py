"""The Review Queue's endpoints (#110): list, answer, skip and undo, for
`current_user` only. Every write returns the queue as it now stands."""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from finance_agent.identity import current_user
from finance_agent.ledger import UnknownTarget
from finance_agent.ledger.review import (
    Answer,
    InvalidAnswer,
    Item,
    ReviewQueue,
    StaleItem,
)


class ItemOut(BaseModel):
    id: str  # "<type>:<key>"; the path parameter of answer and skip
    type: str  # category, subscription or possible_duplicate; more may come
    version: str  # send it back with an answer or skip
    weight: int  # Transactions an answer touches ("Apply to N similar")
    title: str  # the Merchant's name, if any
    merchant_id: int | None
    transaction_ids: list[int]
    choices: list[str]  # empty: answer with a Category
    skipped: bool


class QueueOut(BaseModel):
    items: list[ItemOut]
    remaining: int
    ledger_version: int


class VersionIn(BaseModel):
    version: str


class AnswerIn(VersionIn):
    category: tuple[str, str] | None = None
    transaction_id: int | None = None  # "just this one"
    choice: str | None = None


def _queue(request: Request) -> Iterator[ReviewQueue]:
    with request.app.state.engine.begin() as conn:
        yield ReviewQueue(conn, current_user())


Queue = Annotated[ReviewQueue, Depends(_queue)]
router = APIRouter(prefix="/api/review-queue")


def _item(i: Item) -> ItemOut:
    return ItemOut(
        id=i.id,
        type=i.type,
        version=i.version,
        weight=i.weight,
        title=i.title,
        merchant_id=i.merchant_id,
        transaction_ids=list(i.transaction_ids),
        choices=list(i.choices),
        skipped=i.skipped,
    )


def _out(q: ReviewQueue, change: Callable[[], None] | None = None) -> QueueOut:
    try:
        if change is not None:
            change()
    except UnknownTarget:
        raise HTTPException(404, "no such item, Category or decision") from None
    except StaleItem as e:
        raise HTTPException(409, str(e)) from None
    except InvalidAnswer as e:
        raise HTTPException(422, str(e)) from None
    items = [_item(i) for i in q.items()]
    return QueueOut(items=items, remaining=len(items), ledger_version=q.version())


@router.get("")
def queue(q: Queue) -> QueueOut:
    return _out(q)


@router.post("/undo")
def undo(q: Queue) -> QueueOut:
    """Undo the latest answer or skip."""
    return _out(q, q.undo)


@router.post("/{item_id}/answer")
def answer(q: Queue, item_id: str, body: AnswerIn) -> QueueOut:
    a = Answer(body.category, body.transaction_id, body.choice)
    return _out(q, lambda: q.answer(item_id, body.version, a))


@router.post("/{item_id}/skip")
def skip(q: Queue, item_id: str, body: VersionIn) -> QueueOut:
    return _out(q, lambda: q.skip(item_id, body.version))
