"""The Subscription registry's endpoints (#103): add, remove, regroup, confirm
and dismiss, keyed by Merchant id, for `current_user` only. Every write here is
a user decision, which detection never overwrites."""

from collections.abc import Callable, Iterator
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from pydantic import BaseModel, Field

from finance_agent.categorise.display_names import display_name
from finance_agent.identity import current_user
from finance_agent.ledger import InvalidEntry, Ledger, SubscriptionEntry, UnknownTarget

Kind = Literal["subscription", "bill"]
SQLITE_MAX = 2**63 - 1  # a larger id would be a 500 from the driver, not a 422
MerchantId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]


class EvidenceOut(BaseModel):
    on: str  # ISO date: when detected or when the user decided
    charge_count: int | None
    interval: str | None


class SubscriptionOut(BaseModel):
    merchant_id: int
    merchant: str  # as stored: Manage links and matching use this
    display_name: str  # readable, for showing only (#210)
    kind: str | None  # None for a negative entry
    group: str | None
    status: str  # suggested, active, user_cancelled or not_subscription
    reason: str | None  # known_service, user_confirmed, pattern or user_added
    source: str  # "user" or "detection"
    evidence: EvidenceOut
    created_at: str
    changed_at: str


class GroupIn(BaseModel):
    kind: Kind
    group: str


class AddIn(GroupIn):
    merchant_id: int = Field(ge=1, le=SQLITE_MAX)


class ConfirmIn(BaseModel):  # optional: confirm into another group
    kind: Kind | None = None
    group: str | None = None


def _ledger(request: Request) -> Iterator[Ledger]:
    with request.app.state.engine.begin() as conn:
        yield Ledger(conn, current_user())


Led = Annotated[Ledger, Depends(_ledger)]
router = APIRouter(prefix="/api/subscriptions")


def _out(e: SubscriptionEntry) -> SubscriptionOut:
    return SubscriptionOut(
        merchant_id=e.merchant_id,
        merchant=e.merchant,
        display_name=display_name(e.merchant),
        kind=e.kind,
        group=e.group,
        status=e.status,
        reason=e.reason,
        source=e.source,
        evidence=EvidenceOut(
            on=e.evidence_on.isoformat(),
            charge_count=e.charge_count,
            interval=e.interval,
        ),
        created_at=e.created_at.isoformat(),
        changed_at=e.changed_at.isoformat(),
    )


def apply_change(
    led: Ledger, merchant_id: int, change: Callable[[], object]
) -> SubscriptionOut:
    try:
        change()
    except UnknownTarget:
        raise HTTPException(404, "no such Merchant or registry entry") from None
    except InvalidEntry as e:
        raise HTTPException(422, str(e)) from None
    return next(_out(e) for e in led.subscriptions() if e.merchant_id == merchant_id)


@router.get("")
def subscriptions(led: Led) -> list[SubscriptionOut]:
    """Every entry, negative ("not a subscription") ones included."""
    return [_out(e) for e in led.subscriptions()]


@router.post("", status_code=201)
def add(led: Led, body: AddIn) -> SubscriptionOut:
    m = body.merchant_id
    return apply_change(led, m, lambda: led.add_subscription(m, body.kind, body.group))


@router.patch("/{merchant_id}")
def regroup(led: Led, merchant_id: MerchantId, body: GroupIn) -> SubscriptionOut:
    return apply_change(
        led,
        merchant_id,
        lambda: led.regroup_subscription(merchant_id, body.kind, body.group),
    )


@router.post("/{merchant_id}/confirm")
def confirm(led: Led, merchant_id: MerchantId, body: ConfirmIn) -> SubscriptionOut:
    return apply_change(
        led,
        merchant_id,
        lambda: led.confirm_subscription(merchant_id, body.kind, body.group),
    )


@router.post("/{merchant_id}/dismiss")
def dismiss(led: Led, merchant_id: MerchantId) -> SubscriptionOut:
    """No to a suggestion: remembered as "not a subscription"."""
    return apply_change(led, merchant_id, lambda: led.not_a_subscription(merchant_id))


@router.delete("/{merchant_id}")
def remove(led: Led, merchant_id: MerchantId) -> SubscriptionOut:
    """Take an entry off the list: remembered as "not a subscription"."""
    return apply_change(led, merchant_id, lambda: led.not_a_subscription(merchant_id))
