"""Per-Account figures: the Overview's account card filter (#44)."""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

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
def two_accounts(
    engine: Engine, write_export: Callable[[str], Path]
) -> tuple[str, list[int]]:
    """Both synthetic accounts imported; returns (latest month, account ids)."""
    current, saver = generate_histories(seed=8, days=60)
    files = [write_export(render_export(h, 0, 120).text) for h in (current, saver)]
    with engine.begin() as conn:
        ledger = Ledger(conn, identity.current_user())
        ledger.import_files(files)
        categorise.run(conn, identity.current_user())
        month = ledger.latest_month()
        assert month
        return month, [a.id for a in ledger.accounts()]


def test_account_figures_add_up_to_the_unfiltered_ones(
    client: TestClient, two_accounts: tuple[str, list[int]]
) -> None:
    month, ids = two_accounts
    assert len(ids) == 2

    def get(path: str, **extra: int) -> dict[str, Any]:
        return client.get(path, params={"month": month, **extra}).json()

    whole = get("/api/summary")
    parts = [get("/api/summary", account_id=i) for i in ids]
    for key in ("money_in", "money_out", "net"):
        assert sum(p[key]["minor"] for p in parts) == whole[key]["minor"]
    assert any(p["money_out"]["minor"] != whole["money_out"]["minor"] for p in parts)

    whole_s = get("/api/spending")
    parts_s = [get("/api/spending", account_id=i) for i in ids]
    for key in ("total", "refunds", "fees", "money_out"):
        assert sum(p[key]["minor"] for p in parts_s) == whole_s[key]["minor"]


def test_filtered_gaps_belong_to_that_account(
    engine: Engine, two_accounts: tuple[str, list[int]]
) -> None:
    month, ids = two_accounts
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        every = led.summarise_month(month)
        one = led.summarise_month(month, account_id=ids[0])
    assert all(g.account.id == ids[0] for g in one.gaps)
    assert set(one.gaps) <= set(every.gaps)


def test_accounts_name_their_bank_and_show_only_masked_numbers(
    client: TestClient, two_accounts: tuple[str, list[int]]
) -> None:
    body = client.get("/api/accounts").json()
    assert {a["bank"] for a in body} == {"Lloyds"}
    assert all(a["masked_number"].startswith("••••") for a in body)


def test_unknown_account_is_404_and_bad_id_is_422(
    client: TestClient, two_accounts: tuple[str, list[int]]
) -> None:
    month, _ = two_accounts
    for path in ("/api/summary", "/api/spending"):
        p = {"month": month}
        assert client.get(path, params={**p, "account_id": 9999}).status_code == 404
        assert client.get(path, params={**p, "account_id": "x"}).status_code == 422
