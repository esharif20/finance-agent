# synthetic
"""#212 verifier: a synced Account's card agrees with sync_gaps(), ignores a
sync whose rows were rejected, and lists an Account that holds no rows."""

import json
from collections.abc import Callable
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.ledger import Ledger
from synthetic_lloyds import generate_histories, render_export
from synthetic_simplefin import response
from test_dedupe import temp_ledger

LedgerFor = Callable[[int], Ledger]

H = generate_histories(11, 40)[0]


def pounds(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)


def conflicting(raw: bytes) -> bytes:
    """One row ID twice with different amounts: the sync rejects every row."""
    doc = json.loads(raw)
    rows = doc["accounts"][0]["transactions"]
    rows.append({**rows[0], "amount": "-123.45"})
    return json.dumps(doc).encode()


def test_a_rejected_sync_does_not_move_the_card(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 10))
    report = led.apply_sync(conflicting(response(H, 0, 20)))
    assert report.rejected_accounts == 1 and led.sync_gaps() == []
    (a,) = led.accounts()
    assert a.bank_balance == a.ledger_balance == pounds(H.txns[9].balance_minor)
    assert a.gaps == []


def test_a_balance_only_account_is_listed(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    led.apply_sync(response(H, 5, 5))  # no rows, a reported balance
    (a,) = led.accounts()
    assert a.bank_balance == a.ledger_balance == pounds(H.txns[4].balance_minor)
    assert led.balances_in_gbp().total == a.bank_balance
    assert ledger_for(2).accounts() == []


@settings(max_examples=25, deadline=None)
@given(
    csv_end=st.integers(0, 15),
    first=st.integers(1, 20),
    more=st.integers(1, 19),
    drop=st.sets(st.integers(0, 39), max_size=3),
    offset=st.integers(-9_999, 9_999),
)
def test_a_mixed_account_card_matches_the_latest_sync_gap(
    csv_end: int, first: int, more: int, drop: set[int], offset: int
) -> None:
    """Right after a sync, bank - ledger on the card is the Gap sync_gaps()
    stored for that sync, whatever CSV rows the Account already held."""
    with temp_ledger() as (led, d):
        if csv_end:
            p = d / "export.csv"
            p.write_text(render_export(H, 0, csv_end).text)
            led.import_files([p])
            (csv_account,) = led.accounts()
            led.link_synced_account("ACC-1", csv_account.id)
        led.apply_sync(response(H, 0, first))
        report = led.apply_sync(
            response(H, first - 1, first + more, drop=drop, balance_offset=offset)
        )
        (a,) = led.accounts()
        assert a.bank_balance is not None and a.ledger_balance is not None
        stored = sum((g.amount for g in report.gaps), Decimal(0))
        assert a.bank_balance - a.ledger_balance == stored
        assert sum((g.amount for g in a.gaps), Decimal(0)) == stored
        last = first + more - 1
        assert a.bank_balance == pounds(H.txns[last].balance_minor + offset)
