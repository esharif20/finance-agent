"""The Review Queue's endpoints (#110): list, answer, skip and undo, for
`current_user` only. Every write returns the queue as it now stands."""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from finance_agent.categorise import embedding
from finance_agent.categorise.display_names import display_name
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
    type: str  # category, subscription, possible_duplicate, split_recheck,
    # split_refund or always_ask; more may come
    version: str  # send it back with an answer or skip
    weight: int  # Transactions an answer touches ("Apply to N similar")
    title: str  # the Merchant's name, if any
    display_name: str  # readable title, for showing only (#223)
    merchant_id: int | None
    transaction_ids: list[int]
    choices: list[str]  # empty: answer with a Category
    suggestions: list[
        tuple[str, str]
    ]  # local-model Categories, best first; may be empty
    skipped: bool


class QueueOut(BaseModel):
    items: list[ItemOut]
    remaining: int
    ledger_version: int
    # set when an undo changed nothing because the answer's Correction was
    # already reverted elsewhere; show it instead of "Undid the last change."
    message: str | None = None


class VersionIn(BaseModel):
    version: str


class AnswerIn(VersionIn):
    category: tuple[str, str] | None = None
    transaction_id: int | None = None  # "just this one"
    choice: str | None = None


def _queue(request: Request) -> Iterator[ReviewQueue]:
    with request.app.state.engine.begin() as conn:
        user = current_user()
        yield ReviewQueue(
            conn,
            user,
            suggester=lambda targets, labelled: embedding.suggest(
                conn, user, targets, labelled
            ),
        )


Queue = Annotated[ReviewQueue, Depends(_queue)]
router = APIRouter(prefix="/api/review-queue")


def _item(i: Item) -> ItemOut:
    return ItemOut(
        id=i.id,
        type=i.type,
        version=i.version,
        weight=i.weight,
        title=i.title,
        display_name=display_name(i.title) if i.merchant_id is not None else i.title,
        merchant_id=i.merchant_id,
        transaction_ids=list(i.transaction_ids),
        choices=list(i.choices),
        suggestions=list(i.suggestions),
        skipped=i.skipped,
    )


def _out(q: ReviewQueue, change: Callable[[], str | None] | None = None) -> QueueOut:
    message = None
    try:
        if change is not None:
            message = change()
    except UnknownTarget:
        raise HTTPException(404, "no such item, Category or decision") from None
    except StaleItem as e:
        raise HTTPException(409, str(e)) from None
    except InvalidAnswer as e:
        raise HTTPException(422, str(e)) from None
    items = [_item(i) for i in q.items()]
    return QueueOut(
        items=items, remaining=len(items), ledger_version=q.version(), message=message
    )


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
