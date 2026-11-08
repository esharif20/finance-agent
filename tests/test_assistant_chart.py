"""Inline charts (#317): a figure reply's bars sum to the figure it states."""

from datetime import date
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent.assistant import FakeChatModel, planner
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.assistant.tools.query import CountedRow, QueryPlan, SpendForQueryOut
from finance_agent.assistant.tools.read import Data, Money
from synthetic_assistant import DECEMBER, ME, ROWS, seed

TODAY = date(2026, 1, 20)
PLAN = planner.Plan(kind="figure", since=date(2025, 12, 1), until=date(2025, 12, 31))


def _out(amounts: list[int], other: int) -> SpendForQueryOut:
    rows = [
        CountedRow(
            evidence_id=f"m:{i}",
            merchant_id=i,
            merchant=Data(data=f"Synth {i}"),
            amount=Money(minor=a, currency="GBP"),
        )
        for i, a in enumerate(amounts)
    ]
    total = sum(amounts) + other
    return SpendForQueryOut(
        evidence_id="query:x",
        since="2025-12",
        until="2025-12",
        total=Money(minor=total, currency="GBP"),
        count=len(amounts),
        merchants=rows,
        other=Money(minor=other, currency="GBP"),
        truncated=bool(other),
        not_counted=[],
        unmatched=[],
        plan=QueryPlan(since="2025-12", until="2025-12"),
    )


@given(
    st.lists(st.integers(-50_000, 500_000), min_size=0, max_size=12),
    st.integers(-50_000, 500_000),
)
def test_the_bars_always_sum_to_the_stated_total(
    amounts: list[int], other: int
) -> None:
    out = _out(amounts, other)
    c = planner.chart(PLAN, [planner.Fetched("spend_for_query", out)])
    if c is None:
        assert len(amounts) < 2
        return
    assert sum(b.minor for b in c.bars) == c.total_minor == out.total.minor
    assert len(c.bars) <= planner.CHART_ROWS + 1


def test_a_figure_reply_carries_its_chart(engine: Engine, tmp_path: Path) -> None:
    seed(engine, ME, tmp_path, DECEMBER + ROWS)
    with engine.begin() as conn:
        a = ask(
            Assistant(FakeChatModel([]), conn, TODAY, prefetch=True),
            "How much did I spend on fuel in December 2025?",
        )
    assert a.chart is not None
    bars = {b["label"]: b["minor"] for b in a.chart["bars"]}
    assert sum(bars.values()) == a.chart["total_minor"] == 8400
    assert a.chart["title"] == "Fuel in December 2025"
    assert a.chart["evidence_id"] in a.evidence
