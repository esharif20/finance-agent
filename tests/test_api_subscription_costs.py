"""/api/subscriptions/costs and /cancel (#105), scoped by current_user.
Synthetic data only."""

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.categorise.subscriptions import costs
from finance_agent.ledger import Ledger

Export = Callable[[str], Path]


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def seed(engine: Engine, export: Export, user_id: int = 1) -> int:
    """Netflix charged monthly up to a week ago; returns its Merchant id."""
    last = date.today() - timedelta(days=7)
    rows = "".join(
        f"{last - timedelta(days=30 * i):%d/%m/%Y},NETFLIX.COM,-10.99\n"
        for i in range(3)
    )
    with engine.begin() as conn:
        Ledger(conn, user_id).import_files([export(rows)])
        categorise.run(conn, user_id)
        (e,) = Ledger(conn, user_id).subscriptions()
        return e.merchant_id


def test_costs_list_status_monthly_cost_and_totals(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    m = seed(engine, write_export)
    body = client.get("/api/subscriptions/costs").json()
    (item,) = body["items"]
    assert (item["merchant_id"], item["status"], item["interval"]) == (
        m,
        "active",
        "monthly",
    )
    assert item["monthly"] == {"minor": 1099, "currency": "GBP"}
    assert item["counted"] is True and item["cost_note"] is None
    assert body["totals"] == [
        {"kind": "subscription", "monthly": {"minor": 1099, "currency": "GBP"}}
    ]
    assert body["groups"] == [
        {
            "kind": "subscription",
            "group": "Streaming",
            "monthly": {"minor": 1099, "currency": "GBP"},
        }
    ]
    assert body["rate_needed"] == 0


def test_cancel_takes_it_out_of_the_totals(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    m = seed(engine, write_export)
    r = client.post(f"/api/subscriptions/{m}/cancel")
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["source"]) == ("user_cancelled", "user")
    body = client.get("/api/subscriptions/costs").json()
    assert body["items"][0]["status"] == "cancelled"
    assert body["totals"] == [] and body["groups"] == []
    # confirming lists it as active again
    client.post(f"/api/subscriptions/{m}/confirm", json={})
    assert client.get("/api/subscriptions/costs").json()["items"][0]["counted"]


def test_another_users_subscriptions_are_unreachable(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    """current_user() is 1; user 2's entries and Merchant ids are unknown."""
    theirs = seed(engine, write_export, user_id=2)
    body = client.get("/api/subscriptions/costs").json()
    assert body == {
        "items": [],
        "totals": [],
        "groups": [],
        "likely_monthly": ZERO,
        "rate_needed": 0,
        "flag_count": 0,
        "trial_detection": "partial",
        "total": {"monthly": ZERO, "annual": ZERO},
    }
    assert client.post(f"/api/subscriptions/{theirs}/cancel").status_code == 404
    with engine.begin() as conn:
        (e,) = Ledger(conn, 2).subscriptions()
    assert (e.status, e.source) == ("active", "detection")


def charges(name: str, amounts: list[str], last_days_ago: int = 7) -> str:
    """Monthly charges, oldest first, the last one `last_days_ago` days ago."""
    last = date.today() - timedelta(days=last_days_ago)
    n = len(amounts)
    return "".join(
        f"{last - timedelta(days=30 * (n - 1 - i)):%d/%m/%Y},{name},-{a}\n"
        for i, a in enumerate(amounts)
    )


def seed_rows(engine: Engine, export: Export, rows: str, user_id: int = 1) -> None:
    with engine.begin() as conn:
        Ledger(conn, user_id).import_files([export(rows)])
        categorise.run(conn, user_id)


def test_costs_carry_waste_flags_and_a_flag_count(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    rows = charges("NETFLIX.COM", ["10.99", "10.99", "10.99", "12.99"])
    rows += charges("DISNEY PLUS", ["7.99", "7.99", "7.99"], last_days_ago=3)
    seed_rows(engine, write_export, rows)
    body = client.get("/api/subscriptions/costs").json()
    by = {i["merchant"]: i["flags"] for i in body["items"]}
    rise = {
        "code": "price_rise",
        "on": (date.today() - timedelta(days=7)).isoformat(),
        "before": {"minor": 1099, "currency": "GBP"},
        "after": {"minor": 1299, "currency": "GBP"},
    }
    overlap = {"code": "overlap", "on": None, "before": None, "after": None}
    # the UK merchant base (#215) names these two Merchants
    assert by == {"Netflix": [rise, overlap], "Disney Plus": [overlap]}
    assert body["flag_count"] == 3 and body["trial_detection"] == "partial"


LLOYDS = (
    "Transaction Date,Transaction Type,Sort Code,Account Number,"
    "Transaction Description,Debit Amount,Credit Amount,Balance\n"
)


def test_one_service_charged_on_two_accounts_is_a_duplicate(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    seed(engine, write_export)  # HSBC: Netflix monthly, last a week ago
    on = [date.today() - timedelta(days=d) for d in (63, 33, 3)]
    lloyds = LLOYDS + "".join(
        f"{d:%d/%m/%Y},DEB,00-00-00,00000001,NETFLIX.COM,10.99,,{balance}\n"
        for d, balance in zip(on, ("89.01", "78.02", "67.03"), strict=True)
    )
    seed_rows(engine, write_export, lloyds)
    (item,) = client.get("/api/subscriptions/costs").json()["items"]
    assert [f["code"] for f in item["flags"]] == ["duplicate"]


def test_a_small_first_charge_from_a_known_service_is_a_trial(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    seed_rows(engine, write_export, charges("SPOTIFY", ["0.99"], last_days_ago=10))
    (item,) = client.get("/api/subscriptions/costs").json()["items"]
    started = date.today() - timedelta(days=10)
    assert item["trial"] == {
        "started_on": started.isoformat(),
        "converts_on": (started + timedelta(days=30)).isoformat(),
        "converted": False,
    }


def test_another_users_subscriptions_never_raise_my_flags(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    """User 2's Streaming entry and price rise neither overlap nor show for user 1."""
    seed(engine, write_export)  # user 1: Netflix
    theirs = charges("DISNEY PLUS", ["7.99", "7.99", "9.99"])
    theirs += charges("NETFLIX.COM", ["10.99", "10.99"], last_days_ago=5)
    seed_rows(engine, write_export, theirs, user_id=2)
    body = client.get("/api/subscriptions/costs").json()
    assert [i["merchant"] for i in body["items"]] == ["Netflix"]
    assert body["items"][0]["flags"] == [] and body["flag_count"] == 0


@pytest.mark.parametrize("bad", [0, -1, 2**63, 2**80])
def test_an_out_of_range_merchant_id_is_refused(client: TestClient, bad: int) -> None:
    assert client.post(f"/api/subscriptions/{bad}/cancel").status_code == 422


def test_cancelling_a_negative_or_unknown_entry_is_404(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    m = seed(engine, write_export)
    client.delete(f"/api/subscriptions/{m}")  # "not a subscription"
    assert client.post(f"/api/subscriptions/{m}/cancel").status_code == 404
    assert client.post("/api/subscriptions/999/cancel").status_code == 404


ZERO = {"minor": 0, "currency": "GBP"}


def test_a_single_known_charge_is_estimated_and_totals_round_once(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    """#210: TV Licence's catalogue interval is annual. The annual total is the
    exact yearly sum rounded once, not the rounded monthly figure times 12."""
    on = date.today() - timedelta(days=20)
    seed_rows(engine, write_export, f"{on:%d/%m/%Y},TV LICENCE,-174.50\n")
    body = client.get("/api/subscriptions/costs").json()
    (item,) = body["items"]
    assert (item["interval"], item["interval_source"], item["charge_count"]) == (
        "annual", "catalogue", 1,
    )  # fmt: skip
    assert item["monthly"] == {"minor": 1454, "currency": "GBP"}  # 17450 / 12
    # one charge is likely (#310): its own total, not the main one, until confirmed
    assert (item["status"], item["counted"]) == ("likely", False)
    assert body["likely_monthly"] == {"minor": 1454, "currency": "GBP"}
    assert body["total"]["monthly"] == ZERO
    client.post(f"/api/subscriptions/{item['merchant_id']}/confirm", json={})
    body = client.get("/api/subscriptions/costs").json()
    assert body["likely_monthly"] == ZERO
    assert body["total"] == {
        "monthly": {"minor": 1454, "currency": "GBP"},
        "annual": {"minor": 17450, "currency": "GBP"},
    }


def test_estimates_and_totals_never_cross_users(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    """User 2's single known charge gets its own estimate; user 1 sees none of
    it, and user 1's own unknown single charge says why it has no cost."""
    on = date.today() - timedelta(days=5)
    seed_rows(engine, write_export, f"{on:%d/%m/%Y},NETFLIX.COM,-10.99\n", user_id=2)
    seed_rows(engine, write_export, f"{on:%d/%m/%Y},SYNTH MEMBERSHIP,-4.00\n")
    with engine.begin() as conn:
        (mine,) = Ledger(conn, 1).subscriptions()
    client.post(f"/api/subscriptions/{mine.merchant_id}/confirm", json={})
    body = client.get("/api/subscriptions/costs").json()
    (item,) = body["items"]
    assert item["merchant"] == mine.merchant
    assert (item["interval"], item["cost_note"], item["charge_count"]) == (
        None, "interval_needed", 1,
    )  # fmt: skip
    assert body["total"] == {"monthly": ZERO, "annual": ZERO}
    with engine.begin() as conn:
        (theirs,) = costs(Ledger(conn, 2), date.today()).items
    assert (theirs.interval_source, theirs.monthly) == ("catalogue", 1099)


def test_entries_carry_a_readable_name_and_keep_the_raw_one(
    engine: Engine, client: TestClient, write_export: Export
) -> None:
    """The display name is for showing; `merchant` stays as stored."""
    raw = "SYNTHCO STREAM SUBSCR SYNTHCO STREAM SU"
    seed_rows(engine, write_export, charges(raw, ["6.99", "6.99"]))
    (item,) = client.get("/api/subscriptions/costs").json()["items"]
    (entry,) = client.get("/api/subscriptions").json()
    assert item["merchant"] == entry["merchant"]
    assert raw.startswith(item["merchant"].upper()[:14])  # stored text, not cleaned
    assert item["display_name"] == entry["display_name"] == "Synthco Stream"
