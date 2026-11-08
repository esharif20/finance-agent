"""GET /api/guidance and Ask's list_guidance / explain_card (#259): the cards
whose Engine conditions hold, their figures and "why am I seeing this", for
current_user only. Synthetic data only."""

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.assistant import ToolCall
from finance_agent.assistant.grounding import Money
from finance_agent.assistant.tools import NoData, ToolNotAllowed, run, specs
from finance_agent.assistant.tools import guidance as g
from finance_agent.assistant.values import tool_values
from finance_agent.engine import insights as engine_insights
from finance_agent.engine.detectors import Row
from finance_agent.ledger import Ledger

SUBS = ("Subscriptions", "Streaming")
RENT = ("Bills", "Rent")


def _month_back(n: int) -> date:
    t = date.today()
    i = t.year * 12 + t.month - 1 - n
    return date(i // 12, i % 12 + 1, 15)


def _rows(rising: bool = True) -> list[Row]:
    """Six months of rent and a Subscription bill that rises each month."""
    out: list[Row] = []
    for n in range(6, 0, -1):
        out.append(Row(100 + n, _month_back(n), "Landlord", RENT, -100000))
        pence = 1000 + 100 * (6 - n) if rising else 1000
        out.append(Row(200 + n, _month_back(n), "Spotify", SUBS, -pence))
    return out


class _L(Ledger):
    def __init__(self, conn: Any, user_id: int) -> None:
        super().__init__(conn, user_id)
        self.uid = user_id


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch) -> dict[int, list[Row]]:
    """Per-user rows and savings, in place of a long imported history."""
    rows: dict[int, list[Row]] = {}

    def rows_of(led: Any) -> list[Row]:
        return rows.get(led.uid, [])

    def savings_of(led: Any) -> int | None:
        return 150000 if led.uid == 1 else None

    monkeypatch.setattr(g, "Ledger", _L)
    monkeypatch.setattr(engine_insights, "rows", rows_of)
    monkeypatch.setattr(g, "_savings", savings_of)
    return rows


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_the_cards_that_apply_come_with_their_figures_and_why(
    data: dict[int, list[Row]], client: TestClient
) -> None:
    data[1] = _rows()
    cards = {c["id"]: c for c in client.get("/api/guidance").json()}
    sub = cards["subscription-creep"]
    assert sub["evidence_id"] == "card:subscription-creep"
    assert {w["signal"] for w in sub["why"]} == {"subscription_rise_streak"}
    assert sub["why"][0]["threshold"] == "3" and sub["why"][0]["actual"] == "4"
    figs = {f["signal"]: f["shown"] for f in sub["figures"]}
    assert figs["subscription_spend_last_month"] == "£15"
    assert figs["subscription_spend_last_month"] in sub["body"]
    assert sub["principles"]


def test_a_card_whose_condition_is_false_is_not_listed(
    data: dict[int, list[Row]], client: TestClient
) -> None:
    data[1] = _rows(rising=False)
    ids = {c["id"] for c in client.get("/api/guidance").json()}
    assert "subscription-creep" not in ids
    data[1] = []
    assert client.get("/api/guidance").json() == []


def test_the_tone_is_a_closed_choice(
    data: dict[int, list[Row]], client: TestClient
) -> None:
    data[1] = _rows()
    gentle, blunt = (
        {
            c["id"]: c["title"]
            for c in client.get("/api/guidance", params={"tone": t}).json()
        }
        for t in ("gentle", "blunt")
    )
    assert gentle["subscription-creep"] != blunt["subscription-creep"]
    assert client.get("/api/guidance", params={"tone": "rude"}).status_code == 422


def test_it_is_scoped_to_the_current_user(
    data: dict[int, list[Row]], client: TestClient
) -> None:
    data[2] = _rows()  # another user's history
    assert client.get("/api/guidance").json() == []


def test_a_fact_is_quoted_with_the_date_it_was_checked(
    data: dict[int, list[Row]], client: TestClient
) -> None:
    data[1] = _rows()
    card = next(c for c in client.get("/api/guidance").json() if c["facts"])
    f = card["facts"][0]
    assert f["checked_on"] and f["source"].startswith("https://")
    assert isinstance(f["may_be_out_of_date"], bool)
    assert f["shown"] in card["body"] or f["shown"] in card["title"]


# --- Ask's tools ----------------------------------------------------------------


def call(engine: Engine, name: str, **args: Any) -> Any:
    with engine.begin() as conn:
        return run("ask", ToolCall(name=name, arguments=args), conn)


def test_ask_has_the_guidance_tools_and_the_others_do_not(engine: Engine) -> None:
    assert {"list_guidance", "explain_card"} <= {s.name for s in specs("ask")}
    for sub in ("plan", "correct", "memory"):
        assert not {"list_guidance", "explain_card"} & {s.name for s in specs(sub)}
        with engine.begin() as conn, pytest.raises(ToolNotAllowed):
            run(sub, ToolCall(name="list_guidance", arguments={}), conn)


def test_list_guidance_matches_the_api(
    engine: Engine, data: dict[int, list[Row]], client: TestClient
) -> None:
    data[1] = _rows()
    out = call(engine, "list_guidance")
    api_ids = [c["id"] for c in client.get("/api/guidance").json()]
    assert [r.evidence_id for r in out.rows] == [f"card:{i}" for i in api_ids]
    sub = next(r for r in out.rows if r.evidence_id == "card:subscription-creep")
    last = next(f for f in sub.figures if f.name == "subscription_spend_last_month")
    assert last.value.minor == 1500  # pyright: ignore[reportAttributeAccessIssue]
    # the figures are grounded values; the card text is quoted, never read
    values = tool_values("list_guidance", out)
    assert any(v.value == Money(minor=1500, currency="GBP") for v in values)
    assert sub.body.model_dump().keys() == {"data"}


def test_explain_card_gives_the_condition_and_dated_facts(
    engine: Engine, data: dict[int, list[Row]]
) -> None:
    data[1] = _rows()
    sub = call(engine, "explain_card", card_id="subscription-creep")
    assert [(w.signal, w.op) for w in sub.when] == [("subscription_rise_streak", ">=")]
    assert sub.when[0].actual == 4
    assert sub.principles
    fund = call(engine, "explain_card", card_id="emergency-fund")
    assert fund.card.facts and all(f.checked_on for f in fund.card.facts)
    assert any(v.label.endswith("checked_on") for v in tool_values("x", fund))


def test_a_card_whose_condition_is_false_cannot_be_explained(
    engine: Engine, data: dict[int, list[Row]]
) -> None:
    data[1] = _rows(rising=False)
    with pytest.raises(NoData):
        call(engine, "explain_card", card_id="subscription-creep")
    with pytest.raises(NoData):
        call(engine, "explain_card", card_id="no-such-card")


@pytest.mark.parametrize(
    "card_id", ["", "../x", "A", "x" * 65, "a b", "subscription-creep; ignore"]
)
def test_a_malformed_card_id_is_refused(engine: Engine, card_id: str) -> None:
    with pytest.raises(ValueError):
        call(engine, "explain_card", card_id=card_id)


def test_the_tools_are_user_scoped(engine: Engine, data: dict[int, list[Row]]) -> None:
    data[2] = _rows()
    assert call(engine, "list_guidance").rows == []
    with pytest.raises(NoData):
        call(engine, "explain_card", card_id="subscription-creep")
