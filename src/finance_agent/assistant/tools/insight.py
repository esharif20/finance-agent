"""Ask's Insight tools (#155, spec #134 item 10): the ranked Insights and one
Insight's evidence, as `GET /api/insights` has them. Figures come from code
(engine.insights); the explanation is code's template with `{key}` and
`{impact}` left for the model to fill from the row, so the Merchant's bank text
reaches the model only as `{"data": ...}`.

Read-only: these read the stored Insights and never call `refresh`, which
writes; the feed (`GET /api/insights`) refreshes them."""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from finance_agent.assistant.tools.read import (
    MAX_ROWS,
    Data,
    Money,
    NoData,
    Source,
    TransactionRow,
    txn_row,
)
from finance_agent.engine import insights
from finance_agent.ledger import Ledger, UnknownTarget
from finance_agent.ledger.insights import Insights, StoredInsight

_STRICT = ConfigDict(extra="forbid", frozen=True)


class InsightRow(BaseModel):
    model_config = _STRICT
    evidence_id: str  # insight:<id>
    type: str
    key: Data  # the Merchant, or the Category of a spike
    category: str
    raised_on: date
    impact: Money  # computed by code
    confidence_percent: int
    transactions: list[str]  # txn:<id> evidence, ascending
    template: str  # code's explanation, with {key} and {impact} to fill


def _row(i: StoredInsight) -> InsightRow:
    return InsightRow(
        evidence_id=f"insight:{i.id}",
        type=i.type,
        key=Data(data=i.key),
        category=i.category,
        raised_on=i.raised_on,
        impact=Money(minor=i.impact, currency="GBP"),
        confidence_percent=i.confidence,
        transactions=[f"txn:{t}" for t in i.evidence[:MAX_ROWS]],
        template=insights.TEMPLATES[i.type],
    )


class ListInsightsIn(BaseModel):
    model_config = _STRICT
    limit: int = Field(default=5, ge=1, le=MAX_ROWS, description="most rows")


class ListInsightsOut(BaseModel):
    model_config = _STRICT
    rows: list[InsightRow]  # best first
    total: int
    truncated: bool


def list_insights(src: Source, inp: ListInsightsIn) -> ListInsightsOut:
    ranked = insights.ranked(src.conn, src.user_id)
    return ListInsightsOut(
        rows=[_row(r.insight) for r in ranked[: inp.limit]],
        total=len(ranked),
        truncated=len(ranked) > inp.limit,
    )


class ExplainInsightIn(BaseModel):
    model_config = _STRICT
    insight_id: int = Field(
        ge=1, le=2**63 - 1, description="the number in insight:<id>"
    )


class ExplainInsightOut(BaseModel):
    model_config = _STRICT
    insight: InsightRow
    transactions: list[TransactionRow]  # the evidence, oldest first


def explain_insight(src: Source, inp: ExplainInsightIn) -> ExplainInsightOut:
    try:
        i = Insights(src.conn, src.user_id).get(inp.insight_id)
    except UnknownTarget:
        raise NoData("no such Insight") from None
    led = Ledger(src.conn, src.user_id)
    found = (led.transaction(t) for t in i.evidence[:MAX_ROWS])
    rows = [txn_row(t) for t in found if t is not None]  # None: deleted since
    return ExplainInsightOut(insight=_row(i), transactions=rows)
