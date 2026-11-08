"""Pairwise preferences (#262): ask the next pair, store the answer, show what
was learned in plain words, and reset, for `current_user` only.

"cut" ("which would you cut first?") is over the Subcategories with Spending
in the last `WINDOW_DAYS`, the prior from that spending, as in #255's
evaluation. "matters" ("which matters more?") is over goal Memories and
playbook cards, with no prior. The next pair is `engine.preferences`' most
informative one. Strengths are never shown as numbers: only a rank and words.
Answers are feedback, never Memories. Nothing here logs."""

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent.categorise import seed_taxonomy
from finance_agent.engine import insights, playbook
from finance_agent.engine import preferences as pref
from finance_agent.identity import current_user
from finance_agent.ledger import Ledger
from finance_agent.ledger.pairwise import NotAsked, Pairwise, Question
from finance_agent.ledger.proposals import Proposals

WINDOW_DAYS = 90
PROMPT: dict[Question, str] = {
    "cut": "Which would you cut first?",
    "matters": "Which matters more to you?",
}


class ItemOut(BaseModel):
    key: str  # category:<id>, goal:<id> or card:<id>
    label: str


class PairOut(BaseModel):
    pair_id: int
    question: Question
    prompt: str
    a: ItemOut
    b: ItemOut


class AnswerIn(BaseModel):
    pair_id: int = Field(strict=True, ge=1, le=2**63 - 1)
    choice: str = Field(max_length=80)  # cut: the one cut first; matters: the winner


class AnswerOut(BaseModel):
    pair_id: int
    question: Question


class StrengthOut(BaseModel):
    key: str
    label: str
    rank: int  # 1 is kept longest / matters most
    words: str


class LearnedOut(BaseModel):
    question: Question
    answers: int
    items: list[StrengthOut]  # empty until there is an answer


class DeletedOut(BaseModel):
    deleted: int


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
Q = Annotated[Question, Query()]
router = APIRouter(prefix="/api/preferences")


def items(
    conn: Connection, user: int, question: Question, today: date
) -> tuple[dict[str, str], dict[str, int]]:
    """Item key -> label, in a fixed order, and the spend (pence) the prior
    comes from: none for "matters"."""
    if question == "cut":
        ids = seed_taxonomy(conn, user)
        since = today - timedelta(days=WINDOW_DAYS)
        spent: dict[int, int] = {}
        for r in insights.rows(Ledger(conn, user)):
            if since < r.posted_on <= today and r.category in ids:
                cid = ids[r.category]
                spent[cid] = spent.get(cid, 0) - r.amount
        names = {cid: sub for (_, sub), cid in ids.items()}
        keep = sorted(cid for cid, p in spent.items() if p > 0)
        return (
            {f"category:{c}": names[c] for c in keep},
            {f"category:{c}": spent[c] for c in keep},
        )
    book = playbook.load()
    Pairwise(conn, user).drop_cards({c.id for c in book.cards})
    goals = sorted(
        (m for m in Proposals(conn, user).memories() if m.type == "goal"),
        key=lambda m: m.id,
    )
    labels = {f"goal:{m.id}": m.text for m in goals}
    labels |= {f"card:{c.id}": c.topic.replace("_", " ") for c in book.cards}
    return labels, {}


def fitted(
    conn: Connection, user: int, question: Question, today: date
) -> tuple[dict[str, str], list[pref.Answer], dict[str, int]]:
    """The items, the stored answers about them and the spend for the prior."""
    labels, spend = items(conn, user, question, today)
    answers = [
        pref.Answer(w, lo)
        for w, lo in Pairwise(conn, user).answers(question)
        if w in labels and lo in labels  # an item with no spend lately sits out
    ]
    return labels, answers, spend


@router.get("/next-pair")
def next_pair(conn: Conn, question: Q = "cut") -> PairOut | None:
    """The most informative pair to ask next; None with fewer than two items.
    It replaces the question's unanswered pair."""
    user = current_user()
    labels, answers, spend = fitted(conn, user, question, date.today())
    pair = pref.next_pair(list(labels), answers, pref.prior_from_spend(spend))
    if pair is None:
        return None
    asked = Pairwise(conn, user).ask(question, *pair)
    return PairOut(
        pair_id=asked.id,
        question=question,
        prompt=PROMPT[question],
        a=ItemOut(key=asked.a, label=labels[asked.a]),
        b=ItemOut(key=asked.b, label=labels[asked.b]),
    )


@router.post("/answer")
def answer(conn: Conn, body: AnswerIn) -> AnswerOut:
    """Answer the pair last asked, once. A pair never asked, already answered
    or another user's is not found; a choice outside the pair is refused."""
    try:
        asked = Pairwise(conn, current_user()).answer(body.pair_id, body.choice)
    except NotAsked:
        raise HTTPException(404, "no such pair is waiting for an answer") from None
    except ValueError:
        raise HTTPException(422, "the choice is not one of the pair") from None
    return AnswerOut(pair_id=asked.id, question=asked.question)


def _words(question: Question, rank: int, n: int) -> str:
    top, bottom = rank == 1, rank == n
    if question == "cut":
        if top or bottom:
            return "you'd keep this longest" if top else "you'd cut this first"
        return "you'd rather keep this" if 2 * rank <= n else "you'd cut this sooner"
    if top or bottom:
        return "matters most to you" if top else "matters least to you"
    return "matters more to you" if 2 * rank <= n else "matters less to you"


@router.get("")
def learned(conn: Conn, question: Q = "cut") -> LearnedOut:
    """What the answers say, strongest first, in words (never a number)."""
    labels, answers, spend = fitted(conn, current_user(), question, date.today())
    if not answers:
        return LearnedOut(question=question, answers=0, items=[])
    s = pref.strengths(list(labels), answers, pref.prior_from_spend(spend))
    order = sorted(labels, key=lambda k: (-s[k], k))
    return LearnedOut(
        question=question,
        answers=len(answers),
        items=[
            StrengthOut(
                key=k, label=labels[k], rank=r, words=_words(question, r, len(order))
            )
            for r, k in enumerate(order, 1)
        ],
    )


@router.delete("")
def reset(conn: Conn) -> DeletedOut:
    """Wipe every pairwise question and answer: strengths go back to the prior."""
    return DeletedOut(deleted=Pairwise(conn, current_user()).forget())
