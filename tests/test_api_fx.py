"""The FX endpoints (#156): enter a rate, and the provenance and "rate needed"
state behind the GBP totals. Rates cross the wire as Decimal strings, never
JSON numbers."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ledger import Ledger
from synthetic_banks import revolut_pockets

JAN = {"month": "2026-01"}


def d(day: int) -> date:
    return date(2026, 1, day)


@pytest.fixture
def client(engine: Engine, tmp_path: Path) -> Iterator[TestClient]:
    p = tmp_path / "revolut.csv"
    p.write_text(
        revolut_pockets(
            [
                ("CARD_PAYMENT", "GBP", d(2), "SYNTH SHOP", -1000),
                ("CARD_PAYMENT", "EUR", d(5), "SYNTH CAFE", -2300),
            ]
        )
    )
    with engine.begin() as conn:
        assert Ledger(conn, 1).import_files([p]).rejected == []
    with TestClient(api.create_app(engine)) as c:
        yield c


def put(c: TestClient, **body: Any) -> Any:
    return c.put("/api/fx/rates", json={"currency": "EUR", "on": "2026-01-01"} | body)


def test_a_missing_rate_shows_as_rate_needed_and_the_total_leaves_it_out(
    client: TestClient,
) -> None:
    fx = client.get("/api/fx/month", params=JAN).json()
    assert fx == {
        "month": "2026-01",
        "rates": [],
        "rate_needed": [{"currency": "EUR", "count": 1}],
    }
    assert client.get("/api/summary", params=JAN).json()["money_out"]["minor"] == 1000
    b = client.get("/api/fx/balances").json()
    assert b["total"] is None
    assert [(a["currency"], a["gbp"], a["rate"]) for a in b["accounts"]] == [
        ("EUR", None, None)
    ]


def test_an_entered_rate_converts_with_its_provenance(client: TestClient) -> None:
    r = put(client, rate="0.8")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "currency": "EUR",
        "rate": "0.8",
        "on": "2026-01-01",
        "source": "user",
    }
    assert client.get("/api/fx/rates").json() == [r.json()]
    assert client.get("/api/summary", params=JAN).json()["money_out"]["minor"] == 2840
    fx = client.get("/api/fx/month", params=JAN).json()
    assert fx["rate_needed"] == []
    assert fx["rates"] == [r.json() | {"count": 1}]
    # the Transaction itself stays in euros
    items = client.get("/api/transactions", params=JAN).json()["items"]
    assert sorted((i["amount"]["minor"], i["amount"]["currency"]) for i in items) == [
        (-2300, "EUR"),
        (-1000, "GBP"),
    ]
    b = client.get("/api/fx/balances").json()
    (eur,) = b["accounts"]
    # 977.00 EUR at 0.8; the GBP Account holds 990.00
    assert eur["balance"] == {"minor": 97700, "currency": "EUR"}
    assert eur["gbp"] == {"minor": 78160, "currency": "GBP"}
    assert eur["rate"] == r.json()
    assert b["total"] == {"minor": 99000 + 78160, "currency": "GBP"}


@pytest.mark.parametrize(
    "body",
    [
        {"rate": 0.8},  # a JSON number: floats never carry money
        {"rate": "0"},
        {"rate": "-0.8"},
        {"rate": "NaN"},
        {"rate": "Infinity"},
        {"rate": "1e3"},
        {"rate": "0.0000001"},  # below the sane range
        {"rate": "9999999"},  # above it
        {"rate": "0.8", "currency": "GBP"},
        {"rate": "0.8", "currency": "eur"},
        {"rate": "0.8", "currency": "EURO"},
        {"rate": "0.8", "on": "2026-02-30"},
        {"rate": "0.8", "extra": 1},
        {"rate": "0." + "1" * 40},
    ],
)
def test_a_bad_rate_is_refused_and_nothing_changes(
    client: TestClient, body: dict[str, Any]
) -> None:
    assert put(client, **body).status_code == 422
    assert client.get("/api/fx/rates").json() == []


def test_month_and_account_are_validated(client: TestClient) -> None:
    assert client.get("/api/fx/month", params={"month": "2026-13"}).status_code == 422
    r = client.get("/api/fx/month", params=JAN | {"account_id": 999})
    assert r.status_code == 404
