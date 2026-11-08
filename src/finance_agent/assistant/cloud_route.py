"""The cloud wording path (#301; ADR 0011, 0013, 0016): only for a conversation
the user opted in, and only after this turn's tools ran locally.

`redact` builds the payload (placeholders only), the meter `reserve`s on
`gateway_chat` before every call, `CloudChatModel` writes the wording, then
`validate` and `fill`; the graph runs the unchanged grounding check on the
filled text. Every call that may have reached the host is settled and its
payload logged, whatever happens to the reply. Any refusal returns no text,
and the graph falls back to the local model, then the deterministic table.

Notes never go (no tool returns one yet, so `include_notes` stays False), and
nothing here makes a Proposal: Memory and Correct stay local. Nothing here
logs a value, the question or the reply."""

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol, cast, get_args

from pydantic import BaseModel

from finance_agent.assistant import grounding as g
from finance_agent.gateway import (
    Category,
    LeakError,
    Merchant,
    Money,
    Month,
    Percent,
    Rejected,
    ToolFact,
    fill,
    redact,
    validate,
)
from finance_agent.gateway.cloud import FLASH_MODEL, CloudError, Usage, request_body
from finance_agent.gateway.cost import Allowed, Denied, Reservation, UnknownModel, Warn
from finance_agent.gateway.redact import fold, name_pattern
from finance_agent.gateway.types import FieldName

BUCKET = "gateway_chat"
MAX_TOKENS = 2048  # Gemini's maxOutputTokens counts thinking too
_FIELDS = frozenset(get_args(FieldName.__value__))
type Name = Merchant | Category


class Cloud(Protocol):
    def complete(
        self, payload: Any, *, model: str, max_tokens: int
    ) -> tuple[str, Usage]: ...


class Meter(Protocol):
    """`storage.spend.SpendStore` (the Assistant imports no storage)."""

    def reserve(
        self, bucket: str, model: str, max_in: int, max_out: int
    ) -> Allowed | Warn | Denied: ...

    def settle(self, reservation: Reservation, usage: Usage) -> int: ...

    def log_payload(
        self,
        user_id: int,
        conversation_id: str,
        payload: dict[str, Any],
        model: str,
        cost_pence: int,
    ) -> None: ...


@dataclass(frozen=True)
class Worded:
    text: str | None  # validated and filled; None: fall back
    cost_pence: int | None = None  # None: no call was made
    payload_id: str | None = None  # sha256 of the logged payload's JSON, 16 hex


def payload_id(payload: dict[str, Any]) -> str:
    """Recomputable from `GET /api/gateway/payloads` (stored sort_keys)."""
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def names(out: BaseModel) -> list[Name]:
    """The names a tool result shows: bank or merchant text (`{"data": ...}`)
    as Merchants, Category names as Categories, so `redact` hides them and
    `validate` refuses a reply that writes one out."""
    found: list[Name] = []
    _walk(out.model_dump(), found)
    return found


def _walk(node: Any, found: list[Name]) -> None:
    if isinstance(node, list):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            _walk(item, found)
    elif isinstance(node, dict):
        d: dict[str, Any] = node  # pyright: ignore[reportUnknownVariableType]
        if set(d) == {"data"}:
            if isinstance(d["data"], str) and d["data"].strip():
                found.append(Merchant(name=d["data"]))
            return
        for k, v in d.items():
            if k in ("category", "name") and isinstance(v, str):
                found += [Category(name=p.strip()) for p in v.split(">") if p.strip()]
            else:
                _walk(v, found)


def named_in(
    question: str, merchants: Iterable[str], categories: Iterable[str]
) -> list[Name]:
    """The user's Merchant and Category names the question itself mentions, so
    the redactor cuts them even when no tool returned them (a generic Category
    stays a word; a user-made one becomes {Cn})."""
    text = fold(question)

    def said(n: str) -> bool:
        return bool((p := name_pattern(n)) and re.search(p, text, re.IGNORECASE))

    found: list[Name] = [Merchant(name=n) for n in dict.fromkeys(merchants) if said(n)]
    return found + [Category(name=n) for n in dict.fromkeys(categories) if said(n)]


def _value(v: g.Figure) -> Money | Percent | Month | date | int:
    if isinstance(v, g.Money):
        return Money(minor=v.minor, currency=v.currency)
    if isinstance(v, g.Percent):
        return Percent(value=v.value)
    if isinstance(v, g.Month):
        return Month(year=v.year, month=v.month)
    return v


def facts(values: Sequence[g.ToolValue], shown: Sequence[Name]) -> list[ToolFact]:
    """Labels carry names, so only their last word leaves, and only when it is
    one of the closed `FieldName`s."""
    out = [
        ToolFact(field="merchant" if isinstance(n, Merchant) else "category", value=n)
        for n in shown
    ]
    for v in values:
        last = v.label.rsplit(" ", 1)[-1]
        field = cast(FieldName, last if last in _FIELDS else "value")
        out.append(ToolFact(field=field, value=_value(v.value)))
    return out


@dataclass
class CloudRoute:
    cloud: Cloud
    meter: Meter
    model: str = FLASH_MODEL
    max_tokens: int = MAX_TOKENS

    def word(
        self,
        question: str,
        values: Sequence[g.ToolValue],
        shown: Sequence[Name],
        *,
        user_id: int,
        thread_id: str,
    ) -> Worded:
        try:
            payload, placeholders = redact(question, facts(values, shown))
        except (LeakError, ValueError):  # fails closed: nothing leaves
            return Worded(None)
        # an upper bound: a token is never shorter than a byte of this JSON
        max_in = len(json.dumps(request_body(payload, self.max_tokens)))
        try:
            d = self.meter.reserve(BUCKET, self.model, max_in, self.max_tokens)
        except UnknownModel:
            return Worded(None)
        if isinstance(d, Denied):  # at the cap: no call (ADR 0013)
            return Worded(None)
        try:
            reply, usage = self.cloud.complete(
                payload, model=self.model, max_tokens=self.max_tokens
            )
        except CloudError:
            # it may have reached the host and been billed: charge the whole
            # reservation, so the meter can only over-count
            reply, usage = None, Usage(max_in, self.max_tokens, 0, self.model)
        cost = self.meter.settle(d.reservation, usage)
        sent = payload.model_dump(mode="json")
        self.meter.log_payload(user_id, thread_id, sent, self.model, cost)
        pid = payload_id(sent)
        if reply is None:
            return Worded(None, cost, pid)
        checked = validate(reply, placeholders)
        if isinstance(checked, Rejected):
            return Worded(None, cost, pid)
        return Worded(fill(checked, placeholders), cost, pid)
