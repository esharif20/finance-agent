"""The Transactions behind a spending figure (ADR 0007): what a tool counted,
so the user can check an answer. Built from the same `Ledger.spending_parts`
the tool's total is summed from, so the listed amounts add up to that total
exactly. Shown to the user only; never sent to the model, checkpointed or
logged."""

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel

from finance_agent.assistant.tools.query import SpendForQueryOut
from finance_agent.assistant.tools.read import CategoryDetailOut, SpendingSummaryOut
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
    return next((e for e in counted_all(led, out) if e.evidence_id == _id(out)), None)


def _id(out: BaseModel) -> str:
    return str(getattr(out, "evidence_id", ""))


def counted_all(led: Ledger, out: BaseModel) -> list[Evidence]:
    """Every spending figure in a tool result with the Transactions behind
    it: the total, and each Merchant, Subcategory or Category row. A list is
    kept only when it sums to its figure exactly (ADR 0007), so a figure the
    parts cannot explain (Uncategorised is gross) simply has none."""
    groups: list[tuple[str, int, list[SpendPart]]] = []
    if isinstance(out, CategoryDetailOut):
        top, _, sub = out.category.partition(" > ")
        parts = [
            p
            for p in led.spending_parts(out.month)
            if p.top == top and (not sub or p.sub == sub)
        ]
        groups.append((out.evidence_id, out.total.minor, parts))
        for r in out.subcategories:
            leaf = r.name.rpartition(" > ")[2]
            mine = [p for p in parts if p.sub == leaf]
            groups.append((r.evidence_id, r.amount.minor, mine))
        for r in out.merchants:
            mid = r.evidence_id.rpartition(":")[2]
            mine = [p for p in parts if str(p.merchant_id) == mid]
            groups.append((r.evidence_id, r.amount.minor, mine))
    elif isinstance(out, SpendForQueryOut):
        p = out.plan
        parts = [
            x
            for m in p.months()
            for x in led.spending_parts(m, p.account_id)
            if p.counts(x.top, x.sub, x.merchant_id)
        ]
        groups.append((out.evidence_id, out.total.minor, parts))
        for r in out.merchants:
            mine = [x for x in parts if x.merchant_id == r.merchant_id]
            groups.append((r.evidence_id, r.amount.minor, mine))
    elif isinstance(out, SpendingSummaryOut):
        parts = led.spending_parts(out.month)
        for r in out.categories:
            mine = [x for x in parts if x.top == r.name]
            groups.append((r.evidence_id, r.amount.minor, mine))
    out_list: list[Evidence] = []
    when: dict[int, date] | None = None
    for eid, total, parts in groups:
        by_txn: dict[int, tuple[str, int]] = {}
        for x in parts:  # a Split's counted parts add into one row
            _, was = by_txn.get(x.transaction_id, (x.merchant, 0))
            by_txn[x.transaction_id] = (x.merchant, was + x.spend_minor)
        if sum(m for _, m in by_txn.values()) != total:
            continue  # never a list that does not add up to the figure
        if when is None:
            when = {t.id: t.posted_on for t in led.transactions()}
        rows = sorted(
            (
                Counted(i, when[i], display_name(name), minor)
                for i, (name, minor) in by_txn.items()
                if minor and i in when
            ),
            key=lambda c: (c.posted_on, c.transaction_id),
            reverse=True,
        )
        out_list.append(Evidence(eid, total, tuple(rows)))
    return out_list
