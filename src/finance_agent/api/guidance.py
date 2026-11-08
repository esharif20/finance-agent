"""Guidance cards (#259): the playbook cards whose Engine-evaluated conditions
hold for `current_user`, each with the figures it names and why it is shown.
A thin adapter over `assistant.tools.guidance.evaluate`, which the Assistant's
tools share. It writes only the exposure log (#261): each card shown, once a
week. Nothing here logs to stdout."""

from collections.abc import Iterator
from datetime import date
from fractions import Fraction
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import Connection

from finance_agent.assistant.tools.guidance import Tone, clauses, evaluate, shown_as
from finance_agent.assistant.tools.read import Source
from finance_agent.identity import current_user
from finance_agent.ledger.exposure import Exposures, Shown

CARD_POLICY = "cards-all-v1"  # every card whose condition holds


class FigureOut(BaseModel):
    signal: str
    shown: str  # as the card shows it


class FactOut(BaseModel):
    id: str
    label: str
    shown: str
    source: str  # never fetched
    checked_on: date
    may_be_out_of_date: bool


class WhyOut(BaseModel):
    signal: str
    op: str
    threshold: str
    actual: str


class CardOut(BaseModel):
    evidence_id: str  # card:<id>
    id: str
    topic: str
    tone: str
    title: str
    body: str
    principles: list[str]
    figures: list[FigureOut]
    facts: list[FactOut]
    why: list[WhyOut]  # the condition and the Engine's signal values


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
router = APIRouter(prefix="/api/guidance")


@router.get("")
def guidance(conn: Conn, tone: Annotated[Tone, Query()] = "gentle") -> list[CardOut]:
    user, today = current_user(), date.today()
    g = evaluate(Source(conn, user, today), tone)
    cards = {c.id: c for c in g.book.cards}
    out: list[CardOut] = []
    for s in g.shown:
        why = [
            WhyOut(
                signal=c.signal,
                op=c.op,
                threshold=shown_as(c.signal, c.value),
                actual=shown_as(c.signal, v),
            )
            for c in clauses(cards[s.card_id].when)
            if (v := g.sigs.get(c.signal)) is not None
        ]
        out.append(
            CardOut(
                evidence_id=f"card:{s.card_id}",
                id=s.card_id,
                topic=s.topic,
                tone=s.tone,
                title=s.title,
                body=s.body,
                principles=list(s.principles),
                figures=[FigureOut(signal=n, shown=v) for n, v in s.figures],
                facts=[
                    FactOut(
                        id=f.id,
                        label=f.label,
                        shown=f.value,
                        source=f.source,
                        checked_on=f.checked_on,
                        may_be_out_of_date=f.may_be_out_of_date,
                    )
                    for f in s.facts
                ],
                why=why,
            )
        )
    # every applicable card is shown, so each has propensity 1 (#261)
    Exposures(conn, user).log(
        today,
        CARD_POLICY,
        (
            Shown("card", c.id, k, len(out), Fraction(1), {"tone": c.tone})
            for k, c in enumerate(out)
        ),
    )
    return out
