"""The cost meter (ADR 0013, ADR 0016): pure decisions, no database.

Prices are `Decimal` USD per million tokens; thinking tokens bill at the output
rate. Costs convert to GBP at a deliberately high rate and round up to whole
pence, so the meter can only over-count. `storage.spend` keeps the state and
calls these inside one `BEGIN IMMEDIATE` transaction."""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from finance_agent.gateway.cloud import Usage

Bucket = Literal["synthetic_judge", "gateway_chat"]
CAPS: dict[str, int] = {"synthetic_judge": 150, "gateway_chat": 100}  # pence
WARN_AT = Decimal("0.8")

# Checked against https://ai.google.dev/gemini-api/docs/pricing (paid tier).
PRICES_CHECKED = date(2026, 11, 8)
# Flash is $0.75/$3.75 until 31 Dec 2026; the 1 Jan 2027 price is used now.
# Pro's price is for prompts up to 200k tokens; larger prompts are refused.
PRICES: dict[str, tuple[Decimal, Decimal]] = {  # (input, output) USD per 1M
    "gemini-3.8-flash": (Decimal("1.50"), Decimal("7.50")),
    "gemini-3.1-pro-preview": (Decimal("2.00"), Decimal("12.00")),
}
MAX_INPUT = {"gemini-3.1-pro-preview": 200_000}
# The config aliases (gateway.cloud) as of PRICES_CHECKED; a reply whose
# `modelVersion` is priced higher is charged at that higher price.
ALIASES = {
    "gemini-flash-latest": "gemini-3.8-flash",
    "gemini-pro-latest": "gemini-3.1-pro-preview",
}
GBP_PER_USD = Decimal("0.85")  # above the market rate on PRICES_CHECKED


class UnknownModel(ValueError):
    """No price for this model id: the call is refused."""


@dataclass(frozen=True)
class Reservation:
    bucket: str
    model: str
    month: str  # YYYY-MM, UTC: the month the spend counts against
    pence: int
    id: int = 0  # set by storage.spend


@dataclass(frozen=True)
class Allowed:
    reservation: Reservation


@dataclass(frozen=True)
class Warn:
    """Allowed, but the bucket passes 80% of its cap."""

    reservation: Reservation


@dataclass(frozen=True)
class Denied:
    bucket: str


@dataclass(frozen=True)
class BucketStatus:
    bucket: str
    cap_pence: int
    spent_pence: int
    reserved_pence: int
    state: Literal["ok", "warn", "capped"]


def month_of(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y-%m")  # rolls over on the 1st, UTC


def _price(model: str) -> tuple[Decimal, Decimal]:
    p = PRICES.get(ALIASES.get(model, model))
    if p is None:
        raise UnknownModel("no price for this model")
    return p


def pence(model: str, input_tokens: int, output_tokens: int) -> int:
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts are never negative")
    pin, pout = _price(model)
    usd = (input_tokens * pin + output_tokens * pout) / 1_000_000
    return int((usd * GBP_PER_USD * 100).to_integral_value(ROUND_CEILING))


def _state(cap: int, committed: int) -> Literal["ok", "warn", "capped"]:
    if committed >= cap:
        return "capped"
    return "warn" if committed >= cap * WARN_AT else "ok"


def reserve(
    bucket: str,
    model: str,
    max_in: int,
    max_out: int,
    *,
    committed: int,
    now: datetime,
) -> Allowed | Warn | Denied:
    """`committed` is the month's spend plus open reservations, in pence.
    `max_out` must cover thinking tokens too (Gemini's maxOutputTokens does)."""
    cap = CAPS.get(bucket)
    if cap is None:
        raise ValueError("unknown bucket")
    if max_in > MAX_INPUT.get(ALIASES.get(model, model), max_in):
        raise UnknownModel("this prompt size has no checked price")
    r = Reservation(bucket, model, month_of(now), pence(model, max_in, max_out))
    after = committed + r.pence
    if after > cap:
        return Denied(bucket)
    return Warn(r) if after >= cap * WARN_AT else Allowed(r)


def settle(reservation: Reservation, usage: Usage) -> int:
    """The actual cost in pence, which may exceed the reservation (it is still
    recorded, and the next reserve is then denied)."""
    out = usage.output_tokens + usage.thinking_tokens  # thinking bills as output
    cost = pence(reservation.model, usage.input_tokens, out)
    if ALIASES.get(usage.model_version, usage.model_version) in PRICES:
        cost = max(cost, pence(usage.model_version, usage.input_tokens, out))
    return cost


def month_status(spent: dict[str, int], reserved: dict[str, int]) -> list[BucketStatus]:
    out: list[BucketStatus] = []
    for b, cap in CAPS.items():
        s, r = spent.get(b, 0), reserved.get(b, 0)
        out.append(BucketStatus(b, cap, s, r, _state(cap, s + r)))
    return out
