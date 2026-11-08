"""The weekly briefing (#152): this week's top Insights, forecast band,
upcoming renewals and regret review, for `current_user` only. Computed by
code (`engine.briefing`), shown in the app, never sent anywhere. Regret answers
are feedback, never Memories. Nothing here logs."""

from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent.api.fx import Money
from finance_agent.api.insights import DeletedOut, InsightOut, insight_out
from finance_agent.engine import briefing
from finance_agent.identity import current_user
from finance_agent.ledger.regret import Regret

SQLITE_MAX = 2**63 - 1
TransactionId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]
# the day whose week to brief; today when left out
On = Annotated[date | None, Query()]


class BandOut(BaseModel):
    month: str  # YYYY-MM
    point_minor: int  # pence of money out, from engine.forecast
    lower_minor: int
    upper_minor: int
    recurring_minor: int
    left_out: int  # Categories with too little history, not in the band


class RenewalOut(BaseModel):
    insight_id: int
    merchant: str
    due_on: date
    amount_minor: int  # pence, the last charge


class RegretItemOut(BaseModel):
    transaction_id: int
    posted_on: date
    description: str
    merchant: str | None
    amount: Money
    worth_it: bool | None  # None until answered


class BriefingOut(BaseModel):
    week: date  # its Monday
    insights: list[InsightOut]
    forecast: BandOut | None  # None: not enough history (see `insufficient`)
    next_payday: date | None
    renewals: list[RenewalOut]
    regret: list[RegretItemOut]
    insufficient: list[str]  # "insights", "forecast": too little history yet


class RegretAnswerIn(BaseModel):
    worth_it: bool = Field(strict=True)


class RegretAnswerOut(BaseModel):
    transaction_id: int
    worth_it: bool


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
router = APIRouter(prefix="/api/briefing")


@router.get("")
def weekly(conn: Conn, on: On = None) -> BriefingOut:
    """The briefing for the week holding `on` (today by default)."""
    b = briefing.build(conn, current_user(), on or date.today())
    f = b.forecast
    return BriefingOut(
        week=b.week,
        insights=[insight_out(r) for r in b.insights],
        forecast=None
        if f is None
        else BandOut(
            month=f.month,
            point_minor=f.point,
            lower_minor=f.lower,
            upper_minor=f.upper,
            recurring_minor=f.recurring,
            left_out=f.left_out,
        ),
        next_payday=b.next_payday,
        renewals=[
            RenewalOut(
                insight_id=r.insight_id,
                merchant=r.merchant,
                due_on=r.due_on,
                amount_minor=r.amount,
            )
            for r in b.renewals
        ],
        regret=[
            RegretItemOut(
                transaction_id=t.id,
                posted_on=t.posted_on,
                description=t.description,
                merchant=t.categorisation.merchant if t.categorisation else None,
                amount=Money(minor=int(t.amount.scaleb(2)), currency=t.currency),
                worth_it=b.answered.get(t.id),
            )
            for t in b.regret
        ],
        insufficient=list(b.insufficient),
    )


@router.post("/regret/{transaction_id}")
def answer(
    conn: Conn, transaction_id: TransactionId, body: RegretAnswerIn, on: On = None
) -> RegretAnswerOut:
    """Answer "worth it?" on one of the week's regret items; a later answer
    replaces it. Anything else, including another user's, is not found."""
    user = current_user()
    b = briefing.build(conn, user, on or date.today())
    if transaction_id not in {t.id for t in b.regret}:
        raise HTTPException(404, "not one of this week's regret items")
    Regret(conn, user).answer(transaction_id, b.week, body.worth_it)
    return RegretAnswerOut(transaction_id=transaction_id, worth_it=body.worth_it)


@router.delete("/regret")
def forget_answers(conn: Conn) -> DeletedOut:
    """Wipe every regret answer."""
    return DeletedOut(deleted=Regret(conn, current_user()).forget())
