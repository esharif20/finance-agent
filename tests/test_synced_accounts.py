# synthetic
"""#212: Accounts that come only from bank sync are listed by `accounts()`,
with the balances ADR 0014's reconciliation uses."""

import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api, identity
from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export
from synthetic_simplefin import response
from test_dedupe import temp_ledger

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]

H = generate_histories(7, 40)[0]


def pounds(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)


def with_org(raw: bytes, org: str) -> bytes:
    doc = json.loads(raw)
    doc["accounts"][0]["org"] = {"name": org}
    return json.dumps(doc).encode()


def test_a_synced_only_user_sees_every_synced_account(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 10))
    led.apply_sync(response(H, 0, 6, account_id="ACC-2"))
    led.apply_sync(response(H, 5, 20, pending=2))  # ACC-1 again, later
    accounts = led.accounts()
    assert len(accounts) == 2
    one, two = accounts
    # the latest reported balance less pending rows (what sync_gaps() uses)
    assert one.bank_balance == pounds(H.txns[19].balance_minor)
    assert one.ledger_balance == pounds(H.txns[19].balance_minor)
    assert two.bank_balance == two.ledger_balance == pounds(H.txns[5].balance_minor)
    assert (one.source, one.bank, one.gaps) == ("simplefin", "Synthetic Bank", [])


def test_a_missing_row_shows_as_the_difference_and_one_gap(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 10))
    led.apply_sync(response(H, 8, 20, drop={14}))
    (a,) = led.accounts()
    assert a.bank_balance == pounds(H.txns[19].balance_minor)
    assert a.bank_balance is not None and a.ledger_balance is not None
    assert a.bank_balance - a.ledger_balance == pounds(H.txns[14].amount_minor)
    assert [g.amount for g in a.gaps] == [pounds(H.txns[14].amount_minor)]


@settings(max_examples=30, deadline=None)
@given(
    first=st.integers(1, 20),
    more=st.integers(0, 19),
    pending=st.integers(0, 3),
    offset=st.integers(-50_000, 50_000),
)
def test_the_bank_balance_is_the_reported_balance_and_gaps_explain_the_rest(
    first: int, more: int, pending: int, offset: int
) -> None:
    with temp_ledger() as (led, _):
        led.apply_sync(response(H, 0, first))
        end = first + more
        later = response(
            H, max(0, first - 2), end, pending=pending, balance_offset=offset
        )
        if more:
            led.apply_sync(later)
        (a,) = led.accounts()
        last = end - 1 if more else first - 1
        reported = H.txns[last].balance_minor + (offset if more else 0)
        assert isinstance(a.bank_balance, Decimal)
        assert isinstance(a.ledger_balance, Decimal)
        assert a.bank_balance == pounds(reported)
        assert a.ledger_balance == pounds(H.txns[last].balance_minor)
        assert a.bank_balance - a.ledger_balance == sum(
            (g.amount for g in a.gaps), Decimal(0)
        )


def test_a_linked_csv_account_takes_the_sync_figures(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    """ADR 0014 #212: once an Account has a sync anchor, its balances and Gap
    come from the sync; its CSV running balances no longer decide them."""
    led = ledger_for(1)
    led.import_files([write_export(render_export(H, 0, 30).text)])
    (csv_account,) = led.accounts()
    assert csv_account.bank_balance == pounds(H.txns[29].balance_minor)
    led.link_synced_account("ACC-1", csv_account.id)
    led.apply_sync(response(H, 20, 30))
    led.apply_sync(response(H, 25, 40, balance_offset=-700))
    (a,) = led.accounts()
    assert a.id == csv_account.id
    assert a.bank_balance == pounds(H.txns[39].balance_minor - 700)
    assert a.ledger_balance == pounds(H.txns[39].balance_minor)
    assert [g.amount for g in a.gaps] == [pounds(-700)]


def test_another_user_never_sees_my_synced_accounts(ledger_for: LedgerFor) -> None:
    ledger_for(1).apply_sync(response(H, 0, 10))
    assert ledger_for(2).accounts() == []
    assert len(ledger_for(1).accounts()) == 1


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_api_lists_synced_accounts_with_their_card_art_key(
    engine: Engine, client: TestClient
) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, identity.current_user())
        led.apply_sync(with_org(response(H, 0, 10), "HSBC UK"))
        Ledger(conn, 2).apply_sync(response(H, 0, 10, account_id="OTHER"))
    (a,) = client.get("/api/accounts").json()
    assert a["art"] == "hsbc"
    assert a["bank_balance"]["minor"] == H.txns[9].balance_minor
    assert a["ledger_balance"]["minor"] == H.txns[9].balance_minor
    assert a["source"] == "simplefin"
