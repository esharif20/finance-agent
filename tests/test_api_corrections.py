"""#215 F: GET /api/corrections lists the user's Merchant-wide Corrections beside
what the base, the rules or the model alone would give. Read-only, this user
only, and never a description or an amount. Synthetic payees only."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ledger import Ledger
from test_categorise_embedding import _import  # pyright: ignore[reportPrivateUsage]

FUEL = ("Transport", "Fuel")
GROCERIES = ("Food & Drink", "Groceries")
EATING = ("Food & Drink", "Eating out")


def _seed(engine: Engine, tmp_path: Path) -> dict[str, int]:
    with engine.begin() as c:
        _import(c, tmp_path, 1, [("TESCO PFS 5432", -4000), ("QQX NOODLE BAR", -1234)])
        _import(c, tmp_path, 2, [("ZZQ OTHER USER BAKERY", -999)])
        led = Ledger(c, 1)
        ids = {m.name: m.id for m in led.merchants()}
        led.correct_merchant(ids["Tesco Fuel"], GROCERIES)
        led.correct_merchant(ids["QQX NOODLE BAR"], EATING)
        other = Ledger(c, 2)
        other.correct_merchant(other.merchants()[0].id, EATING)
        return ids


def test_each_correction_comes_with_what_base_rules_and_model_say(
    engine: Engine, tmp_path: Path
) -> None:
    ids = _seed(engine, tmp_path)
    with TestClient(api.create_app(engine)) as client:
        r = client.get("/api/corrections")
    assert r.status_code == 200
    got = {c["merchant_id"]: c for c in r.json()["corrections"]}
    assert set(got) == {ids["Tesco Fuel"], ids["QQX NOODLE BAR"]}  # user 1 only
    tesco = got[ids["Tesco Fuel"]]
    assert (tesco["category"], tesco["base"]) == (list(GROCERIES), list(FUEL))
    noodle = got[ids["QQX NOODLE BAR"]]
    assert noodle["category"] == list(EATING)
    assert noodle["base"] is None and noodle["model"] is None
    assert set(tesco) == {"id", "merchant_id", "category", "base", "rules", "model"}
    text = json.dumps(r.json())
    for leak in ("TESCO PFS", "5432", "40.00", "4000", "NOODLE", "12.34"):
        assert leak not in text


def test_the_category_filter_takes_a_taxonomy_id(
    engine: Engine, tmp_path: Path
) -> None:
    ids = _seed(engine, tmp_path)
    with TestClient(api.create_app(engine)) as client:
        one = client.get(
            "/api/corrections", params={"category": "Food & Drink/Eating out"}
        )
        bad = client.get(
            "/api/corrections", params={"category": "Food & Drink/Rockets"}
        )
    assert [c["merchant_id"] for c in one.json()["corrections"]] == [
        ids["QQX NOODLE BAR"]
    ]
    assert bad.status_code == 422


def test_a_reverted_correction_is_not_listed(engine: Engine, tmp_path: Path) -> None:
    ids = _seed(engine, tmp_path)
    with engine.begin() as c:
        led = Ledger(c, 1)
        k = next(k for k in led.corrections() if k.merchant_id == ids["QQX NOODLE BAR"])
        led.revert_correction(k.id)
    with TestClient(api.create_app(engine)) as client:
        got = client.get("/api/corrections").json()["corrections"]
    assert [c["merchant_id"] for c in got] == [ids["Tesco Fuel"]]
