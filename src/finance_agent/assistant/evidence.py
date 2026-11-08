"""The Transactions behind a spending figure (ADR 0007): what a tool counted,
so the user can check an answer. Built from the same `Ledger.spending_parts`
the tool's total is summed from, so the listed amounts add up to that total
exactly. Shown to the user only; never sent to the model, checkpointed or
logged."""

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from finance_agent.assistant.tools.query import SpendForQueryOut
from finance_agent.assistant.tools.read import CategoryDetailOut
from finance_agent.categorise.display_names import display_name
from finance_agent.ledger import Ledger, SpendPart


@dataclass(frozen=True)
class Counted:
    transaction_id: int
    posted_on: date
    merchant: str  # display name
    amount_minor: int  # spending in GBP; negative for a Refund


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    total_minor: int  # the tool's own total
    transactions: tuple[Counted, ...]  # newest first; they sum to total_minor


def counted(led: Ledger, out: BaseModel) -> Evidence | None:
    """The Transactions a spending total counted; None for any other output."""
    parts: list[SpendPart] = []
    if isinstance(out, CategoryDetailOut):
        top, _, sub = out.category.partition(" > ")
        parts = [
            p
            for p in led.spending_parts(out.month)
            if p.top == top and (not sub or p.sub == sub)
        ]
    elif isinstance(out, SpendForQueryOut):
        p = out.plan
        parts = [
            x
            for m in p.months()
            for x in led.spending_parts(m, p.account_id)
            if p.counts(x.top, x.sub, x.merchant_id)
        ]
    else:
        return None
    by_txn: dict[int, tuple[str, int]] = {}
    for x in parts:  # a Split's counted parts add into one row
        _, was = by_txn.get(x.transaction_id, (x.merchant, 0))
        by_txn[x.transaction_id] = (x.merchant, was + x.spend_minor)
    when = {t.id: t.posted_on for t in led.transactions() if t.id in by_txn}
    rows = sorted(
        (
            Counted(i, when[i], display_name(name), minor)
            for i, (name, minor) in by_txn.items()
            if minor and i in when
        ),
        key=lambda c: (c.posted_on, c.transaction_id),
        reverse=True,
    )
    return Evidence(out.evidence_id, out.total.minor, tuple(rows))
