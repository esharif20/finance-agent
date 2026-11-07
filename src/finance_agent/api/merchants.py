"""The Merchant endpoints (#125): list Merchants with their "Always ask" flag
and suggestion, and set or clear the flag, for `current_user` only. Only the
user sets the flag; the app suggests it and never sets it itself."""

from collections.abc import Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.identity import current_user
from finance_agent.ledger import Ledger, Merchant, UnknownTarget

SQLITE_MAX = 2**63 - 1
MerchantId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]

SWITCHED_OFF = (
    "Always ask is on, so this Merchant's Merchant-wide Correction is off. "
    "Corrections to single payments stay."
)
ON = "Always ask is on: each payment here waits in To review for your answer."
OFF = "Always ask is off. Nothing switched off earlier is restored automatically."


class MerchantOut(BaseModel):
    id: int
    name: str
    default_category: tuple[str, str] | None
    always_ask: bool
    always_ask_source: Literal["user", "suggestion"] | None
    # corrected to two or more different Categories; a suggestion only
    suggest_always_ask: bool


class MerchantsOut(BaseModel):
    items: list[MerchantOut]
    ledger_version: int


class AlwaysAskIn(BaseModel):
    on: bool = Field(strict=True)
    # "suggestion": the user accepted the app's suggestion
    source: Literal["user", "suggestion"] = "user"


class AlwaysAskOut(BaseModel):
    merchant: MerchantOut
    switched_off_corrections: list[int]  # Merchant-wide Corrections now reverted
    message: str
    ledger_version: int


def _ledger(request: Request) -> Iterator[tuple[Connection, Ledger]]:
    with request.app.state.engine.begin() as conn:
        yield conn, Ledger(conn, current_user())


Led = Annotated[tuple[Connection, Ledger], Depends(_ledger)]
router = APIRouter(prefix="/api/merchants")


def _out(m: Merchant, suggested: set[int]) -> MerchantOut:
    return MerchantOut(
        id=m.id,
        name=m.name,
        default_category=m.default_category,
        always_ask=m.always_ask is not None,
        always_ask_source=m.always_ask,  # pyright: ignore[reportArgumentType]
        suggest_always_ask=m.id in suggested,
    )


@router.get("")
def merchants(led: Led) -> MerchantsOut:
    _, ledger = led
    suggested = ledger.always_ask_suggested()
    return MerchantsOut(
        items=[_out(m, suggested) for m in ledger.merchants()],
        ledger_version=ledger.ledger_version(),
    )


@router.put("/{merchant_id}/always-ask")
def set_always_ask(
    led: Led, merchant_id: MerchantId, body: AlwaysAskIn
) -> AlwaysAskOut:
    conn, ledger = led
    try:
        reverted = ledger.set_always_ask(merchant_id, body.on, body.source)
    except UnknownTarget:
        raise HTTPException(404, "no such Merchant") from None
    categorise.run(conn, current_user())  # abstain now, or categorise again
    m = next(m for m in ledger.merchants() if m.id == merchant_id)
    return AlwaysAskOut(
        merchant=_out(m, ledger.always_ask_suggested()),
        switched_off_corrections=reverted,
        message=(SWITCHED_OFF if reverted else ON) if body.on else OFF,
        ledger_version=ledger.ledger_version(),
    )
