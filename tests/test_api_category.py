"""GET /api/categories/{name}: the drill-down, a thin adapter over the Ledger."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise, identity
from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


@pytest.fixture
def seeded(engine: Engine, write_export: Callable[[str], Path]) -> str:
    h, _ = generate_histories(seed=8, days=60)
    with engine.begin() as conn:
        ledger = Ledger(conn, identity.current_user())
        ledger.import_files([write_export(render_export(h, 0, 120).text)])
        categorise.run(conn, identity.current_user())
        return ledger.latest_month() or ""


def minor(d: Decimal) -> int:
    return int(d.scaleb(2))


def test_figures_match_the_ledger(
    client: TestClient, engine: Engine, seeded: str
) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        tops = led.spending_by_category(seeded)
        top = next(s for s in tops if s.amount > 0 and s.children)
        want = led.category_detail(top.name, seeded)
        assert want is not None
        in_month = [
            t
            for t in led.transactions()
            if f"{t.posted_on:%Y-%m}" == seeded
            and t.categorisation
            and t.categorisation.category
            and t.categorisation.category[0] == top.name
            and t.categorisation.kind in ("Spending", "Refund")
        ]

    body = client.get(f"/api/categories/{top.name}", params={"month": seeded}).json()

    assert body["name"] == top.name
    assert body["total"]["minor"] == minor(top.amount)
    assert [(s["name"], s["amount"]["minor"]) for s in body["subcategories"]] == [
        (c.name, minor(c.amount)) for c in top.children
    ]
    subs = sum(s["amount"]["minor"] for s in body["subcategories"])
    assert subs == body["total"]["minor"]
    assert [(m["name"], m["amount"]["minor"]) for m in body["merchants"]] == [
        (m.name, minor(m.amount)) for m in want.merchants
    ]
    assert len(body["merchants"]) <= 5
    assert body["trend"][-1]["month"] == seeded
    assert body["trend"][-1]["amount"]["minor"] == body["total"]["minor"]
    assert len(body["trend"]) == 6
    assert sorted(t["id"] for t in body["transactions"]) == sorted(
        t.id for t in in_month
    )
    assert body["transactions"] == sorted(
        body["transactions"], key=lambda t: (t["posted_on"], t["id"]), reverse=True
    )


def test_unknown_category_and_bad_month(client: TestClient, seeded: str) -> None:
    got = client.get("/api/categories/Nope", params={"month": seeded})
    assert got.status_code == 404
    got = client.get("/api/categories/Housing", params={"month": "2026-13"})
    assert got.status_code == 422
