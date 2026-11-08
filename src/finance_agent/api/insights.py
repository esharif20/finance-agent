"""The Insight feed (#150): list the ranked Insights, rate one, dismiss one, for
`current_user` only. Listing refreshes the stored Insights from the Ledger
first (idempotent). Only a rating or a dismissal teaches relevance; listing
never does. Nothing here logs."""

from collections.abc import Iterator
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent.engine import insights
from finance_agent.identity import current_user
from finance_agent.ledger import UnknownTarget
from finance_agent.ledger.insights import Insights, Signal

SQLITE_MAX = 2**63 - 1
InsightId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]
MAX_SHOWN = 20  # ADR 0015: exposure is capped


class RelevanceOut(BaseModel):
    liked: int  # posterior Beta(1 + liked, 1 + disliked)
    disliked: int


class InsightOut(BaseModel):
    id: int
    type: str
    key: str  # the Merchant, or the Category of a spike
    display_name: str  # readable key, for showing only (#223)
    category: str
    raised_on: date
    impact_minor: int  # pence, computed by code
    confidence: int  # percent
    evidence: list[int]  # Transaction ids
    explanation: str
    signal: Signal | None
    relevance: RelevanceOut


class FeedbackIn(BaseModel):
    liked: bool = Field(strict=True)


class FeedbackOut(BaseModel):
    id: int
    signal: Signal


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
router = APIRouter(prefix="/api/insights")


@router.get("")
def ranked(
    conn: Conn, limit: Annotated[int, Query(ge=1, le=MAX_SHOWN)] = 5
) -> list[InsightOut]:
    """The best `limit` Insights, by impact x confidence x relevance, less
    those a constraint Memory rules out."""
    user = current_user()
    insights.refresh(conn, user)
    return [insight_out(r) for r in insights.ranked(conn, user)[:limit]]


def insight_out(r: insights.Ranked) -> InsightOut:
    i = r.insight
    return InsightOut(
        id=i.id,
        type=i.type,
        key=i.key,
        display_name=insights.shown_key(i),
        category=i.category,
        raised_on=i.raised_on,
        impact_minor=i.impact,
        confidence=i.confidence,
        evidence=list(i.evidence),
        explanation=insights.explain(i),
        signal=i.signal,
        relevance=RelevanceOut(liked=r.relevance.liked, disliked=r.relevance.disliked),
    )


def _give(conn: Connection, insight_id: int, signal: Signal) -> FeedbackOut:
    try:
        i = Insights(conn, current_user()).give(insight_id, signal)
    except UnknownTarget:
        raise HTTPException(404, "no such Insight") from None
    return FeedbackOut(id=i.id, signal=signal)


@router.post("/{insight_id}/feedback")
def feedback(conn: Conn, insight_id: InsightId, body: FeedbackIn) -> FeedbackOut:
    """A thumbs-up or thumbs-down; a later one replaces it."""
    return _give(conn, insight_id, "up" if body.liked else "down")


@router.post("/{insight_id}/dismiss")
def dismiss(conn: Conn, insight_id: InsightId) -> FeedbackOut:
    """Hide the Insight; it counts as a thumbs-down for its type and Category."""
    return _give(conn, insight_id, "dismissed")


class DeletedOut(BaseModel):
    deleted: int


@router.delete("/feedback")
def forget_feedback(conn: Conn) -> DeletedOut:
    """Wipe every rating and dismissal: relevance starts again from Beta(1, 1)."""
    return DeletedOut(deleted=Insights(conn, current_user()).forget_feedback())
