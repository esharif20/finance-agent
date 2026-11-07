"""Synthetic SimpleFIN responses built from a synthetic_lloyds History, so CSV
exports and sync responses describe the same purchases."""

import json
from collections.abc import Collection
from datetime import datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from synthetic_lloyds import History, Txn

LONDON = ZoneInfo("Europe/London")


def posted_ts(h: History, i: int) -> int:
    """Row i posts at noon London plus i seconds: unique, ordered instants."""
    t = h.txns[i]
    return int(datetime.combine(t.date, time(12), LONDON).timestamp()) + i


def provider_id(i: int) -> str:
    return f"{i:05d}"  # all digits with leading zeros: must stay opaque text


def _money(minor: int) -> str:
    return str(Decimal(minor).scaleb(-2))


def sync_row(t: Txn, pid: str, ts: int) -> dict[str, Any]:
    return {
        "id": pid,
        "posted": ts,
        "amount": _money(t.amount_minor),
        "description": t.description.title(),  # case differs from the CSV
        "transacted_at": ts - 3600,
    }


def response(
    h: History,
    start: int,
    end: int,
    *,
    account_id: str = "ACC-1",
    drop: Collection[int] = (),
    pending: int = 0,
    balance_offset: int = 0,
) -> bytes:
    """Rows h.txns[start:end] (minus `drop`), balance as of the last row. As in
    spike #26, the balance includes the pending rows (-1.00 each)."""
    rows = [
        sync_row(h.txns[i], provider_id(i), posted_ts(h, i))
        for i in range(start, end)
        if i not in drop
    ]
    rows += [
        {
            "id": f"pend{n}",
            "posted": 0,
            "amount": "-1.00",
            "description": "PENDING",
            "pending": True,
        }
        for n in range(pending)
    ]
    last = end - 1
    return json.dumps(
        {
            "errors": [],
            "accounts": [
                {
                    "org": {"name": "Synthetic Bank"},
                    "id": account_id,
                    "name": "Synthetic Current",
                    "currency": "GBP",
                    "balance": _money(
                        h.txns[last].balance_minor - 100 * pending + balance_offset
                    ),
                    "balance-date": posted_ts(h, last),
                    "transactions": rows,
                }
            ],
        }
    ).encode()
