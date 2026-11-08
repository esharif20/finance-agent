"""The read-only tools (docs/AI_DESIGN.md, ADR 0007). Each calls the same Ledger
read the dashboard endpoint calls, so a tool's figure is the API's figure, and
returns integer minor units plus currency, at most MAX_ROWS rows, each with an
evidence ID that names what it is (stable for the same data).

Purpose notes (#124) are left out on purpose: no output model has a field for
them. When they land, add an opt-in `include_notes` input and a `note: Data`
field, so notes go through the same sanitiser and never reach the model by
default."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Connection

from finance_agent.assistant.tools.sanitise import sanitise
from finance_agent.ledger import (
    CURRENCY,
    Breakdown,
    CategoryDetail,
    MonthSummary,
    SubscriptionEntry,
    Transaction,
    TransactionPage,
)

MAX_ROWS = 20
_STRICT = ConfigDict(extra="forbid", frozen=True)
Month = Annotated[str, Field(pattern=r"^\d{4}-\d{2}$")]
# Field descriptions are the only part of a tool the model reads besides its name
TOP_LEVEL = "a top-level Category name"


class Reads(Protocol):
    """The only Ledger methods a tool may call: reads, scoped by the Ledger."""

    def months(self) -> list[str]: ...
    def summarise_month(
        self, month: str, account_id: int | None = None
    ) -> MonthSummary: ...
    def category_breakdown(
        self, month: str, account_id: int | None = None
    ) -> Breakdown: ...
    def category_detail(
        self,
        name: str,
        month: str,
        trend: int = 6,
        top_merchants: int = 5,
        account_id: int | None = None,
    ) -> CategoryDetail | None: ...
    def subscriptions(self) -> list[SubscriptionEntry]: ...
    def search_transactions(
        self,
        account_id: int | None = None,
        kind: str | None = None,
        category: str | None = None,
        month: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> TransactionPage: ...


@dataclass(frozen=True)
class Source:
    """What an engine-backed tool reads from (#155): the user's own data, as of
    the turn's day. Those tools call engine reads only, never `refresh`."""

    conn: Connection
    user_id: int
    today: date


class NoData(LookupError):
    """Nothing of the user's matches: no transactions, or none that month."""


class Data(BaseModel):
    """Bank-supplied text: quoted data for the model, never instructions.
    Sanitised on construction, so no unsanitised text can sit in one."""

    model_config = _STRICT
    data: str

    @field_validator("data")
    @classmethod
    def _clean(cls, v: str) -> str:
        return sanitise(v)


class Money(BaseModel):
    model_config = _STRICT
    minor: int
    currency: str


def _money(amount: Decimal, currency: str = CURRENCY) -> Money:
    return Money(minor=int(amount.scaleb(2)), currency=currency)  # as the API


def _month(led: Reads, month: str | None) -> str:
    known = led.months()
    month = month or (known[0] if known else None)
    if month is None or month not in known:
        raise NoData("no transactions that month")
    return month


# --- spending_summary ------------------------------------------------------


class SpendingSummaryIn(BaseModel):
    model_config = _STRICT
    month: Month | None = Field(
        default=None,
        description="YYYY-MM, e.g. 2026-01; default the latest month with transactions",
    )


class CategoryRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    name: str
    amount: Money  # spending after refunds; Uncategorised is gross


class SpendingSummaryOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    month: str
    money_in: Money  # own-account Transfers left out, as /api/summary
    money_out: Money
    net: Money
    spending: Money  # after refunds, as /api/spending's total
    refunds: Money
    fees: Money
    unmatched_refunds: Money
    categories: list[CategoryRow]
    truncated: bool
    # an import Gap touches the month (/api/summary's gaps), or a foreign
    # Transaction is left out for want of a rate (/api/fx/month's rate_needed)
    incomplete: bool


def spending_summary(led: Reads, inp: SpendingSummaryIn) -> SpendingSummaryOut:
    m = _month(led, inp.month)
    s, b = led.summarise_month(m), led.category_breakdown(m)
    rows = [
        CategoryRow(
            evidence_id=f"cat:{m}:{c.name}", name=c.name, amount=_money(c.amount)
        )
        for c in b.categories
    ]
    return SpendingSummaryOut(
        evidence_id=f"month:{m}",
        month=m,
        money_in=_money(s.money_in),
        money_out=_money(s.money_out),
        net=_money(s.net),
        spending=_money(b.spending_after_refunds),
        refunds=_money(b.refunds),
        fees=_money(b.fees),
        unmatched_refunds=_money(b.unmatched_refunds),
        categories=rows[:MAX_ROWS],
        truncated=len(rows) > MAX_ROWS,
        incomplete=bool(s.gaps) or s.rate_needed > 0,
    )


# --- category_trend --------------------------------------------------------


class CategoryTrendIn(BaseModel):
    model_config = _STRICT
    category: str = Field(
        min_length=1, max_length=40, description=TOP_LEVEL + ", e.g. Food & Drink"
    )
    month: Month | None = Field(
        default=None, description="YYYY-MM the trend ends at; default the latest"
    )
    months: int = Field(default=6, ge=1, le=12, description="how many months")


class TrendRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    month: str
    amount: Money


class CategoryTrendOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    category: str
    month: str
    total: Money  # this month's, as the Category drill-down shows it
    points: list[TrendRow]  # oldest first
    truncated: bool


def category_trend(led: Reads, inp: CategoryTrendIn) -> CategoryTrendOut:
    m = _month(led, inp.month)
    d = led.category_detail(inp.category, m, trend=inp.months)
    if d is None:
        raise NoData("no spending in that Category that month")
    rows = [
        TrendRow(evidence_id=f"trend:{d.name}:{p}", month=p, amount=_money(a))
        for p, a in d.trend
    ]
    return CategoryTrendOut(
        # not "cat:": the drill-down's Uncategorised is net, the breakdown's gross
        evidence_id=f"detail:{m}:{d.name}",
        category=d.name,
        month=m,
        total=_money(d.total),
        points=rows[-MAX_ROWS:],
        truncated=len(rows) > MAX_ROWS,
    )


# --- list_subscriptions ----------------------------------------------------


class ListSubscriptionsIn(BaseModel):
    model_config = _STRICT


class SubscriptionRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    merchant: Data
    kind: str | None  # "subscription" or "bill"
    group: str | None
    status: str  # suggested, active or user_cancelled
    interval: str | None
    charge_count: int | None
    since: date


class ListSubscriptionsOut(BaseModel):
    model_config = _STRICT
    rows: list[SubscriptionRow]
    total: int
    truncated: bool


def list_subscriptions(led: Reads, inp: ListSubscriptionsIn) -> ListSubscriptionsOut:
    entries = [e for e in led.subscriptions() if e.status != "not_subscription"]
    rows = [
        SubscriptionRow(
            evidence_id=f"sub:{e.merchant_id}",
            merchant=Data(data=e.merchant),
            kind=e.kind,
            group=e.group,
            status=e.status,
            interval=e.interval,
            charge_count=e.charge_count,
            since=e.created_at.date(),
        )
        for e in entries[:MAX_ROWS]
    ]
    return ListSubscriptionsOut(
        rows=rows, total=len(entries), truncated=len(entries) > MAX_ROWS
    )


# --- find_transactions -----------------------------------------------------


class FindTransactionsIn(BaseModel):
    model_config = _STRICT
    query: str | None = Field(
        default=None, max_length=100, description="words in the description or Merchant"
    )
    month: Month | None = Field(default=None, description="YYYY-MM")
    category: str | None = Field(default=None, max_length=40, description=TOP_LEVEL)
    kind: Literal["Spending", "Income", "Transfer", "Refund", "Fee"] | None = Field(
        default=None, description="the Transaction's kind"
    )
    limit: int = Field(
        default=MAX_ROWS, ge=1, le=MAX_ROWS, description="most rows to return"
    )


class TransactionRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    posted_on: str  # ISO date
    description: Data
    merchant: Data | None
    amount: Money  # signed as the Bank has it: spending negative
    kind: str | None
    category: list[str] | None  # [top, sub]


class FindTransactionsOut(BaseModel):
    model_config = _STRICT
    rows: list[TransactionRow]  # newest first
    total: int  # every match, before the limit
    truncated: bool


def txn_row(t: Transaction) -> TransactionRow:
    c = t.categorisation
    return TransactionRow(
        evidence_id=f"txn:{t.id}",
        posted_on=t.posted_on.isoformat(),
        description=Data(data=t.description),
        merchant=Data(data=c.merchant) if c and c.merchant else None,
        amount=_money(t.amount, t.currency),  # its own currency
        kind=c.kind if c else None,
        category=list(c.category) if c and c.category else None,
    )


def find_transactions(led: Reads, inp: FindTransactionsIn) -> FindTransactionsOut:
    page = led.search_transactions(
        None, inp.kind, inp.category, inp.month, inp.query, inp.limit
    )
    rows = [txn_row(t) for t in page.items]
    return FindTransactionsOut(
        rows=rows, total=page.total, truncated=page.total > len(rows)
    )
