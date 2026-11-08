"""Read-only spend and gateway payload views (#295): where each ADR 0013
bucket stands this month, and what a conversation sent to the cloud
(placeholders only). Pence are integers."""

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from finance_agent.identity import current_user
from finance_agent.storage.spend import SpendStore

router = APIRouter(prefix="/api")


class BucketOut(BaseModel):
    bucket: str
    cap_pence: int
    spent_pence: int
    reserved_pence: int
    state: Literal["ok", "warn", "capped"]


class PayloadOut(BaseModel):
    conversation_id: str
    payload: dict[str, Any]
    model: str
    cost_pence: int
    sent_at: str  # ISO, UTC


def _store(request: Request) -> SpendStore:
    db = getattr(getattr(request.app.state.engine, "url", None), "database", None)
    if not db or db == ":memory:":
        raise HTTPException(404, "No spend store for this ledger.")
    return SpendStore.beside(Path(db))


@router.get("/spend")
def spend(request: Request) -> list[BucketOut]:
    return [BucketOut(**vars(s)) for s in _store(request).month_status()]


@router.get("/gateway/payloads")
def payloads(
    request: Request,
    conversation: Annotated[str, Query(min_length=1, max_length=64)],
) -> list[PayloadOut]:
    rows = _store(request).list_payloads(current_user(), conversation)
    return [
        PayloadOut(
            conversation_id=p.conversation_id,
            payload=p.payload,
            model=p.model,
            cost_pence=p.cost_pence,
            sent_at=p.sent_at.isoformat(),
        )
        for p in rows
    ]
