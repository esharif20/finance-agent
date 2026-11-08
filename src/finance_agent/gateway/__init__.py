"""The privacy gateway (ADR 0011): pure functions, no database, no network.

`redact` turns tool values and a question into a placeholder payload,
`validate` checks a cloud reply against the map, and `fill` puts the real
values back locally."""

from finance_agent.gateway.redact import LeakError, redact
from finance_agent.gateway.types import (
    Category,
    CloudReply,
    GatewayPayload,
    Merchant,
    Money,
    Month,
    Note,
    Percent,
    PlaceholderMap,
    Rejected,
    ToolFact,
)
from finance_agent.gateway.validate import fill, validate

__all__ = [
    "Category",
    "CloudReply",
    "GatewayPayload",
    "LeakError",
    "Merchant",
    "Money",
    "Month",
    "Note",
    "Percent",
    "PlaceholderMap",
    "Rejected",
    "ToolFact",
    "fill",
    "redact",
    "validate",
]
