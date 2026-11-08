"""The demo looks like the real app (#299): every screen has synthetic data,
three Demo Bank cards per Persona, dates ending today, a handful of Review
items, and an Assistant that answers from tool results with no model."""

import json
import shutil
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import keyring
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import synthetic_history as sh
from build_demo_db import build as build_personas
from finance_agent.demo import app as demo
from finance_agent.demo import session
from finance_agent.demo.session import Visitor, sign

SECRET = "t" * 40
HOST = "https://testserver"
TODAY = date(2026, 11, 8)


@pytest.fixture(scope="module")
def personas(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    held = keyring.get_keyring()
    root = tmp_path_factory.mktemp("demo-data")
    build_personas(root, histories=2, months=14, today=TODAY)
    keyring.set_keyring(held)
    yield root / "personas"


@pytest.fixture
def app(tmp_path: Path, personas: Path) -> Iterator[FastAPI]:
    root = tmp_path / "demo"
    shutil.copytree(personas, root / "personas")
    (root / "web").mkdir()  # a web build, mounted at "/" as on a deploy
    (root / "web" / "index.html").write_text("<!doctype html><title>demo</title>")
    held = keyring.get_keyring()
    env = {"FA_DEMO": "1", "FA_DEMO_DIR": str(root), "FA_DEMO_SECRET": SECRET}
    yield demo.build(env)
    keyring.set_keyring(held)


def client(app: FastAPI, persona: int) -> TestClient:
    c = TestClient(app, base_url=HOST, headers={"origin": HOST})
    c.cookies.set(
        session.COOKIE, sign(SECRET.encode(), Visitor(f"{persona:032x}", persona))
    )
    return c


@pytest.mark.parametrize("persona", [0, 1])
def test_every_screen_has_data(app: FastAPI, persona: int) -> None:
    c = client(app, persona)
    months = c.get("/api/months").json()
    assert months[0] == "2026-11" and len(months) >= 12  # the latest is this month
    txns = c.get("/api/transactions", params={"limit": 500}).json()["items"]
    assert max(t["posted_on"] for t in txns) <= TODAY.isoformat()

    accounts = c.get("/api/accounts").json()
    assert sorted(a["name"] for a in accounts) == ["Credit card", "Everyday", "Savings"]
    assert {a["bank"] for a in accounts} == {"Demo Bank"}
    assert len({a["masked_number"] for a in accounts}) == 3

    subs = c.get("/api/subscriptions").json()
    ai = [s for s in subs if s["merchant"].endswith(" AI")]
    assert len(ai) >= 3
    assert 1 <= len(c.get("/api/review-queue").json()["items"]) <= 8
    assert len(c.get("/api/memories").json()["items"]) == 3
    assert c.get("/api/insights").json()
    assert c.get("/api/cash-flow/forecast").json()
    spending = {
        x["name"]
        for x in c.get("/api/spending", params={"month": months[1]}).json()[
            "categories"
        ]
    }
    assert {"Bills", "Food & Drink", "Transport", "Subscriptions"} <= spending


def test_the_card_bill_leaves_the_everyday_balances_as_generated(
    personas: Path,
) -> None:
    """The card payment comes out of each payday's sweep, so every other row of
    the current account keeps the balance synthetic_history gave it."""
    from build_demo_db import CARD_PAYMENT, accounts, present  # noqa: PLC0415

    with present(14, TODAY) as (total, since):
        (h,) = sh.generate(histories=1, months=total)
        current, card, savings = accounts(h, TODAY, since)
    assert current[0][0] >= since == date(2025, 10, 1)  # 14 months to Nov
    sweep = "SAVINGS SWEEP"
    want = [
        t.balance for t in h.txns if since <= t.date <= TODAY and t.merchant != sweep
    ]
    got = [b for _, text, _, b in current if text not in (sweep, CARD_PAYMENT)]
    assert got == want
    assert any(text == CARD_PAYMENT for _, text, _, _ in current)
    assert sum(a for *_, a, _ in card) == card[-1][3]  # the card runs from zero
    assert all(b <= 0 for *_, b in card)  # paid in full, never in credit
    assert savings[-1][3] > savings[0][3]


@pytest.mark.parametrize(
    ("question", "tool", "category"),
    [
        (
            "How much did I spend on groceries last month?",
            "category_detail",
            "Groceries",
        ),
        ("What did I spend on fuel?", "category_detail", "Fuel"),
        ("How much do my AI tools cost?", "category_detail", "AI tools"),
        ("What are my subscriptions?", "list_subscriptions", None),
        ("How much did I spend?", "spending_summary", None),
    ],
)
def test_a_question_maps_to_a_read_tool(
    question: str, tool: str, category: str | None
) -> None:
    call = demo.recorded_call(question, TODAY)
    assert call.name == tool
    assert call.arguments.get("category") == category


def test_the_assistant_answers_from_tool_results(app: FastAPI) -> None:
    r = client(app, 0).post(
        "/api/chat", json={"message": "How much did I spend on groceries last month?"}
    )
    assert r.status_code == 200
    meta = json.loads(r.text.split("event: meta\ndata: ", 1)[1].split("\n", 1)[0])
    assert meta["path"] == "deterministic"
    assert "Groceries" in r.text and "local model" not in r.text


def test_reset_and_shuffle_reach_their_routes_past_the_web_build(app: FastAPI) -> None:
    """The web build is mounted at "/": a POST to /api/demo/* must not land on
    the static files (405)."""
    c = client(app, 0)
    assert c.post("/api/demo/reset").status_code == 200
    assert c.post("/api/demo/shuffle").status_code == 200
    assert c.get("/").status_code == 200
