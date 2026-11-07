"""Verifier #181 (#156): adversarial FX cases. Amounts are hundredths in every
currency, so a currency whose minor unit is not a hundredth is refused, never
stored 100x off; GBP-only Revolut exports keep their identities; exchange legs
pair only as exchanges and never move money in or out; rates stay per user."""

import hashlib
import json
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api, ingest
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger
from finance_agent.ledger.fx import InvalidRate
from synthetic_banks import revolut_export, revolut_pockets

JAN = "2026-01"


def d(day: int) -> date:
    return date(2026, 1, day)


def load(led: Ledger, tmp: Path, rows: list[tuple[str, str, date, str, int]]) -> None:
    p = tmp / f"revolut-{len(list(tmp.iterdir()))}.csv"
    p.write_text(revolut_pockets(rows))
    assert led.import_files([p]).rejected == []


@pytest.fixture
def led(ledger_for: Callable[[int], Ledger]) -> Ledger:
    return ledger_for(1)


# --- zero- and three-decimal currencies: refused, never 100x off -----------

NOT_HUNDREDTHS = ["JPY", "KRW", "ISK", "VND", "KWD", "BHD", "TND", "CLF", "XAU"]


@pytest.mark.parametrize("cur", NOT_HUNDREDTHS)
def test_revolut_refuses_a_currency_not_in_hundredths(cur: str) -> None:
    p = ingest.parse(revolut_export(1, 2, currency=cur).encode())
    assert p.rows == []
    assert {r.reason for r in p.rejected} == {"currency without a two-digit minor unit"}


@pytest.mark.parametrize("cur", NOT_HUNDREDTHS)
def test_simplefin_skips_an_account_not_in_hundredths(cur: str) -> None:
    acc: dict[str, Any] = {
        "org": {"name": "Example Bank"},
        "id": "A1",
        "name": "Current",
        "currency": cur,
        "balance": "1500",
        "balance-date": 1772366400,
        "transactions": [],
    }
    r = simplefin.parse(json.dumps({"errors": [], "accounts": [acc]}).encode())
    assert (r.accounts, r.skipped) == ([], 1)


@pytest.mark.parametrize("cur", NOT_HUNDREDTHS)
def test_no_rate_is_taken_for_a_currency_not_in_hundredths(
    led: Ledger, cur: str
) -> None:
    with pytest.raises(InvalidRate):
        led.set_rate(cur, Decimal("0.005"), d(1))
    assert led.fx_rates() == []


@pytest.mark.parametrize("cur", ["EUR", "USD", "CHF", "SEK", "PLN"])
def test_two_digit_currencies_still_import(cur: str) -> None:
    assert ingest.parse(revolut_export(1, 2, currency=cur).encode()).rejected == []


# --- a GBP-only Revolut export keeps every identity it had before FX -------


@settings(max_examples=25, deadline=None)
@given(st.integers(0, 10_000), st.integers(1, 30))
def test_gbp_rows_keep_their_pre_fx_account_ref_and_masked_number(
    seed: int, n: int
) -> None:
    """Byte-identical to the pre-#156 formula: sha256("revolut:<Product>")."""
    p = ingest.parse(revolut_export(seed, n).encode())
    assert p.rejected == []
    old_ref = hashlib.sha256(b"revolut:Current").hexdigest()
    assert {(r.account_ref, r.masked_number, r.currency) for r in p.rows} == {
        (old_ref, "Current", "GBP")
    }


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(st.integers(0, 10_000), st.integers(1, 15))
def test_reimporting_a_gbp_export_adds_no_accounts_or_rows(
    engine: Engine, tmp_path: Path, seed: int, n: int
) -> None:
    """Imported once, again, and again inside a mixed-currency export: the GBP
    Account and its rows are the same, and only the EUR pocket is new."""
    with engine.connect() as conn:
        trans = conn.begin()
        led = Ledger(conn, 1)
        gbp = tmp_path / f"gbp-{seed}-{n}.csv"
        gbp.write_text(revolut_export(seed, n))
        led.import_files([gbp])
        before = [(a.id, a.currency) for a in led.accounts()]
        rows = [
            (t.id, t.posted_on, t.amount, t.description) for t in led.transactions()
        ]
        again = tmp_path / f"gbp-again-{seed}-{n}.csv"  # same rows, new file hash
        again.write_text(revolut_export(seed, n) + "\n")
        assert led.import_files([again]).rejected == []
        assert [(a.id, a.currency) for a in led.accounts()] == before
        assert [
            (t.id, t.posted_on, t.amount, t.description) for t in led.transactions()
        ] == rows
        trans.rollback()


# --- exchange legs: paired only as exchanges, never moving money -----------


def outs(led: Ledger) -> tuple[Decimal, Decimal]:
    s = led.summarise_month(JAN)
    return s.money_in, s.money_out


def test_two_exchanges_into_one_currency_on_one_day_pair_and_imply_one_rate(
    led: Ledger, tmp_path: Path
) -> None:
    load(
        led,
        tmp_path,
        [
            ("EXCHANGE", "GBP", d(1), "Exchanged to EUR", -10000),
            ("EXCHANGE", "EUR", d(1), "Exchanged from GBP", 11500),
            ("EXCHANGE", "GBP", d(1), "Exchanged to EUR", -20000),
            ("EXCHANGE", "EUR", d(1), "Exchanged from GBP", 23000),
            ("CARD_PAYMENT", "EUR", d(2), "SYNTH CAFE", -2300),
        ],
    )
    assert outs(led) == (Decimal("0.00"), Decimal("20.00"))  # only the cafe
    (r,) = led.fx_rates()
    assert (r.currency, r.gbp_per_unit * 115, r.source) == ("EUR", 100, "exchange")
    assert sum(t.transfer_with is not None for t in led.transactions()) == 4


def test_an_exchange_each_way_through_gbp_on_one_day_never_counts_as_money(
    led: Ledger, tmp_path: Path
) -> None:
    """EUR -> GBP and GBP -> USD the same day: four Transfer legs, no money in
    or out, and no rate (which GBP leg went with which is not knowable)."""
    load(
        led,
        tmp_path,
        [
            ("EXCHANGE", "EUR", d(1), "Exchanged to GBP", -5000),
            ("EXCHANGE", "GBP", d(1), "Exchanged from EUR", 4300),
            ("EXCHANGE", "GBP", d(1), "Exchanged to USD", -10000),
            ("EXCHANGE", "USD", d(1), "Exchanged from GBP", 12700),
        ],
    )
    assert outs(led) == (Decimal("0.00"), Decimal("0.00"))
    assert all(t.transfer_with is not None for t in led.transactions())
    by_id = {t.id: t for t in led.transactions()}
    for t in by_id.values():
        assert t.transfer_with is not None
        assert by_id[t.transfer_with].currency != t.currency
    assert led.fx_rates() == []


def test_a_lone_exchange_leg_stays_unpaired(led: Ledger, tmp_path: Path) -> None:
    load(led, tmp_path, [("EXCHANGE", "GBP", d(1), "Exchanged to EUR", -10000)])
    (t,) = led.transactions()
    assert t.transfer_with is None
    assert outs(led) == (Decimal("0.00"), Decimal("100.00"))
    assert led.fx_rates() == []


def test_same_currency_exchange_legs_are_not_an_exchange(
    led: Ledger, tmp_path: Path
) -> None:
    """Two GBP EXCHANGE rows of different amounts, one in each of two Products:
    not a currency exchange, and not an own-account Transfer either."""
    p = tmp_path / "two-products.csv"
    lines = revolut_pockets(
        [
            ("EXCHANGE", "GBP", d(1), "Exchanged", -10000),
            ("EXCHANGE", "GBP", d(1), "Exchanged", 9000),
        ]
    ).splitlines()
    lines[3] = lines[3].replace(",Current,", ",Savings,")
    p.write_text("\n".join(lines) + "\n")
    assert led.import_files([p]).rejected == []
    assert len(led.accounts()) == 2
    assert all(t.transfer_with is None for t in led.transactions())
    assert led.fx_rates() == []


spend = st.tuples(st.integers(1, 28), st.integers(-50_000, 50_000).filter(bool))


@settings(max_examples=30, deadline=None)
@given(
    st.lists(spend, max_size=10),
    st.lists(
        st.tuples(
            st.integers(1, 28), st.sampled_from(["EUR", "USD"]), st.integers(1, 9000)
        ),
        max_size=4,
    ),
)
def test_linked_exchanges_leave_money_in_and_out_as_the_gbp_spends_alone(
    spends: list[tuple[int, int]], exchanges: list[tuple[int, str, int]]
) -> None:
    import tempfile

    from finance_agent.storage import open_db

    rows = [("CARD_PAYMENT", "GBP", d(day), "SYNTH", a) for day, a in spends]
    for day, cur, gbp in exchanges:
        rows += [
            ("EXCHANGE", "GBP", d(day), f"Exchanged to {cur}", -gbp),
            ("EXCHANGE", cur, d(day), "Exchanged from GBP", gbp + gbp // 5 + 1),
        ]
    rows.sort(key=lambda r: r[2])
    with tempfile.TemporaryDirectory() as tmp:
        engine = open_db(Path(tmp) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            if rows:
                load(led, Path(tmp), rows)
            s = led.summarise_month(JAN)
            assert s.money_in == Decimal(sum(a for _, a in spends if a > 0)).scaleb(-2)
            assert s.money_out == Decimal(-sum(a for _, a in spends if a < 0)).scaleb(
                -2
            )
            assert s.transfers_out == Decimal(sum(g for *_, g in exchanges)).scaleb(-2)
        engine.dispose()


# --- rates are the user's own ----------------------------------------------


def test_another_users_rates_and_exchanges_never_reach_this_user(
    engine: Engine, tmp_path: Path
) -> None:
    cafe = ("CARD_PAYMENT", "EUR", d(5), "SYNTH CAFE", -2300)
    with engine.begin() as conn:
        other = Ledger(conn, 2)
        other.set_rate("EUR", Decimal("0.8"), d(1))
        load(
            other,
            tmp_path,
            [
                ("EXCHANGE", "GBP", d(1), "Exchanged to EUR", -10000),
                ("EXCHANGE", "EUR", d(1), "Exchanged from GBP", 11500),
            ],
        )
        assert len(other.fx_rates()) == 2
        load(Ledger(conn, 1), tmp_path, [cafe])
    with TestClient(api.create_app(engine)) as c:  # current_user() is 1
        assert c.get("/api/fx/rates").json() == []
        fx = c.get("/api/fx/month", params={"month": JAN}).json()
        assert fx["rates"] == [] and fx["rate_needed"] == [
            {"currency": "EUR", "count": 1}
        ]
        assert (
            c.get("/api/summary", params={"month": JAN}).json()["money_out"]["minor"]
            == 0
        )
        r = c.put(
            "/api/fx/rates", json={"currency": "EUR", "rate": "0.5", "on": "2026-01-01"}
        )
        assert r.status_code == 200
    with engine.begin() as conn:  # user 1's entry replaced nothing of user 2's
        other = Ledger(conn, 2)
        assert [(r.gbp_per_unit, r.source) for r in other.fx_rates()][-1] == (
            Decimal("0.8"),
            "user",
        )


@pytest.mark.parametrize(
    "rate",
    [1e-3, 1, True, None, "0.8 ", " 0.8", "+0.8", "0x1", "1_000", "0.8\n", "١.٥"],
)
def test_put_rate_refuses_anything_but_plain_ascii_decimal_digits(
    engine: Engine, rate: Any
) -> None:
    with TestClient(api.create_app(engine)) as c:
        r = c.put(
            "/api/fx/rates", json={"currency": "EUR", "rate": rate, "on": "2026-01-01"}
        )
        assert r.status_code == 422
        assert c.get("/api/fx/rates").json() == []
