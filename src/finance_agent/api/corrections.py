"""Read-only list of the user's Merchant-wide Corrections (#215 F), each beside
what the base, the rules or the model alone would say. `current_user` only;
no descriptions, no amounts."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import Connection

from finance_agent.categorise import TAXONOMY
from finance_agent.categorise.corrections import corrected
from finance_agent.identity import current_user


class CorrectionVsModelOut(BaseModel):
    id: int
    merchant_id: int
    category: tuple[str, str]
    base: tuple[str, str] | None
    rules: tuple[str, str] | None
    model: tuple[str, str] | None


class CorrectionsVsModelOut(BaseModel):
    corrections: list[CorrectionVsModelOut]


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
router = APIRouter(prefix="/api/corrections")


@router.get("")
def list_corrections(
    conn: Conn,
    category: Annotated[str | None, Query(max_length=80)] = None,
) -> CorrectionsVsModelOut:
    """`category`: "Top/Sub" (taxonomy ids), to list one Category's only."""
    want: tuple[str, str] | None = None
    if category is not None:
        top, _, sub = category.partition("/")
        if sub not in TAXONOMY.get(top, ()):
            raise HTTPException(422, "category must be a current 'Top/Sub' id")
        want = (top, sub)
    return CorrectionsVsModelOut(
        corrections=[
            CorrectionVsModelOut(
                id=c.id,
                merchant_id=c.merchant_id,
                category=c.category,
                base=c.base,
                rules=c.rules,
                model=c.model,
            )
            for c in corrected(conn, current_user(), want)
        ]
    )
