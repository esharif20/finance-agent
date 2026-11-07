"""FX conversion with provenance (#156): Decimal only, no egress, a missing
rate is "rate needed" and never guessed."""

import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import categorise, ingest
from finance_agent.ledger import Ledger
from finance_agent.ledger.fx import SOURCES, InvalidRate, check_rate, convert
from finance_agent.storage import open_db
from synthetic_banks import revolut_pockets

amounts = st.integers(-(10**12), 10**12)
rates = st.decimals(
    min_value=Decimal("0.0001"),
    max_value=Decimal("10000"),
    places=6,
    allow_nan=False,
    allow_infinity=False,
)


def test_convert_rounds_half_to_even_in_whole_minor_units() -> None:
    assert convert(1000, Decimal("1.15")) == 1150
    assert convert(-1, Decimal("0.5")) == 0  # -0.5 -> 0, half to even
    assert convert(3, Decimal("0.5")) == 2  # 1.5 -> 2
    assert convert(1, Decimal("0.8547")) == 1


@given(amounts, rates)
def test_converting_round_trips_within_one_minor_unit(x: int, r: Decimal) -> None:
    """From the currency whose minor unit is worth more, there and back lands
    within one of its minor units (the finer side cannot lose more)."""
    coarse_to_fine = r if r >= 1 else 1 / r
    back = convert(convert(x, coarse_to_fine), 1 / coarse_to_fine)
    assert abs(back - x) <= 1


@pytest.mark.parametrize(
    "bad",
    ["0", "-1.15", "-0", "NaN", "sNaN", "Infinity", "-Infinity", "1e999999", "1e-30"],
)
def test_a_zero_negative_or_non_finite_rate_is_refused(bad: str) -> None:
    with pytest.raises(InvalidRate):
        check_rate(Decimal(bad))
    with pytest.raises(InvalidRate):
        convert(100, Decimal(bad))


# --- the Ledger: where a rate comes from -----------------------------------

JAN = "2026-01"


def d(day: int) -> date:
    return date(2026, 1, day)


def load(led: Ledger, tmp: Path, rows: list[tuple[str, str, date, str, int]]) -> None:
    p = tmp / f"revolut-{len(list(tmp.iterdir()))}.csv"
    p.write_text(revolut_pockets(rows))
    report = led.import_files([p])
    assert report.rejected == []


EXCHANGE = [  # 100.00 GBP bought 115.00 EUR on 1 Jan
    ("EXCHANGE", "GBP", d(1), "Exchanged to EUR", -10000),
    ("EXCHANGE", "EUR", d(1), "Exchanged from GBP", 11500),
]
CAFE = ("CARD_PAYMENT", "EUR", d(5), "SYNTH CAFE", -2300)


@pytest.fixture
def led(ledger_for: Callable[[int], Ledger]) -> Ledger:
    return ledger_for(1)


def test_a_foreign_spend_counts_in_gbp_at_the_users_own_exchange_rate(
    led: Ledger, tmp_path: Path
) -> None:
    load(led, tmp_path, [*EXCHANGE, CAFE])
    s = led.summarise_month(JAN)
    assert s.currency == "GBP"
    assert s.money_out == Decimal("20.00")  # 23.00 EUR at 100/115
    assert s.money_in == Decimal("0.00")
    # the exchange is a Transfer between the user's own pockets
    assert (s.transfers_out, s.transfers_in) == (Decimal("100.00"), Decimal("100.00"))
    fx = led.fx_month(JAN)
    assert fx.rate_needed == ()
    cafe = next(c for c in fx.conversions if c.amount_minor == -2300)
    assert (cafe.currency, cafe.gbp_minor) == ("EUR", -2000)
    assert (cafe.rate.source, cafe.rate.on) == ("exchange", d(1))


def test_a_missing_rate_is_rate_needed_never_guessed(
    led: Ledger, tmp_path: Path
) -> None:
    load(led, tmp_path, [CAFE])
    s = led.summarise_month(JAN)
    assert s.money_out == Decimal("0.00")  # left out, not converted at 1:1
    fx = led.fx_month(JAN)
    assert fx.conversions == ()
    assert [t.currency for t in fx.rate_needed] == ["EUR"]
    assert all(sp.amount == 0 for sp in led.spending_by_category(JAN))


def test_a_user_rate_fills_a_missing_rate_from_its_date_on(
    led: Ledger, tmp_path: Path
) -> None:
    load(led, tmp_path, [CAFE])
    led.set_rate("EUR", Decimal("0.9"), d(6))  # after the spend: does not apply
    assert len(led.fx_month(JAN).rate_needed) == 1
    v = led.ledger_version()
    led.set_rate("EUR", Decimal("0.8"), d(1))
    assert led.ledger_version() > v  # caches key off it (ADR 0010)
    assert led.summarise_month(JAN).money_out == Decimal("18.40")
    (c,) = led.fx_month(JAN).conversions
    assert (c.rate.source, c.rate.gbp_per_unit, c.rate.on) == (
        "user",
        Decimal("0.8"),
        d(1),
    )
    led.set_rate("EUR", Decimal("0.5"), d(1))  # re-entering replaces it
    assert led.summarise_month(JAN).money_out == Decimal("11.50")


def test_the_users_exchange_beats_an_entered_rate(led: Ledger, tmp_path: Path) -> None:
    led.set_rate("EUR", Decimal("0.5"), d(1))
    load(led, tmp_path, [*EXCHANGE, CAFE])
    assert led.summarise_month(JAN).money_out == Decimal("20.00")


def test_the_banks_own_gbp_amount_beats_every_other_rate(
    led: Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source 1: an Importer that carries the GBP charged (no export in the repo
    does yet) has it used as is, ahead of the exchange rate."""
    load(led, tmp_path, EXCHANGE)
    real = ingest.parse

    def with_gbp(data: bytes) -> ingest.ParsedFile:
        p = real(data)
        return replace(p, rows=[replace(r, gbp_minor=-1999) for r in p.rows])

    monkeypatch.setattr(ingest, "parse", with_gbp)
    load(led, tmp_path, [CAFE])
    (c,) = [c for c in led.fx_month(JAN).conversions if c.amount_minor == -2300]
    assert (c.gbp_minor, c.rate.source, c.rate.on) == (-1999, "bank", d(5))
    assert led.summarise_month(JAN).money_out == Decimal("19.99")


@pytest.mark.parametrize("gbp", [0, 1999])  # zero, or the wrong sign
def test_a_nonsense_bank_amount_is_never_used(
    led: Ledger, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gbp: int
) -> None:
    real = ingest.parse

    def with_gbp(data: bytes) -> ingest.ParsedFile:
        p = real(data)
        return replace(p, rows=[replace(r, gbp_minor=gbp) for r in p.rows])

    monkeypatch.setattr(ingest, "parse", with_gbp)
    load(led, tmp_path, [CAFE])
    assert len(led.fx_month(JAN).rate_needed) == 1


def test_one_leg_of_an_exchange_implies_no_rate(led: Ledger, tmp_path: Path) -> None:
    load(led, tmp_path, [EXCHANGE[1], CAFE])  # the GBP side was never imported
    assert len(led.fx_month(JAN).rate_needed) == 2  # the EUR leg and the spend


def test_exchanges_into_two_currencies_on_one_day_imply_no_rate(
    led: Ledger, tmp_path: Path
) -> None:
    """Which GBP leg bought which currency is not knowable: no rate, not a guess."""
    load(
        led,
        tmp_path,
        [
            *EXCHANGE,
            ("EXCHANGE", "GBP", d(1), "Exchanged to USD", -10000),
            ("EXCHANGE", "USD", d(1), "Exchanged from GBP", 12700),
            CAFE,
        ],
    )
    needed = sorted(t.currency for t in led.fx_month(JAN).rate_needed)
    assert needed == ["EUR", "EUR", "USD"]  # both legs and the spend


@pytest.mark.parametrize("bad", ["0", "-0.8", "NaN", "Infinity", "1e9"])
def test_a_zero_negative_or_absurd_entered_rate_is_refused(
    led: Ledger, bad: str
) -> None:
    v = led.ledger_version()
    with pytest.raises(InvalidRate):
        led.set_rate("EUR", Decimal(bad), d(1))
    assert led.fx_rates() == [] and led.ledger_version() == v


@pytest.mark.parametrize("cur", ["GBP", "eur", "EU", "EURO", "€€€"])
def test_a_rate_for_gbp_or_a_malformed_currency_is_refused(
    led: Ledger, cur: str
) -> None:
    with pytest.raises(InvalidRate):
        led.set_rate(cur, Decimal("0.8"), d(1))


def test_a_split_foreign_spend_keeps_its_gbp_total_exact(
    engine: Engine, tmp_path: Path
) -> None:
    """Parts are converted by share with the remainder on the last, so the
    Categories still add up to Money out to the penny."""
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        led.set_rate("EUR", Decimal("0.3333"), d(1))
        load(led, tmp_path, [("CARD_PAYMENT", "EUR", d(5), "SYNTH SHOP", -1000)])
        categorise.run(conn, 1)
        (t,) = led.transactions()
        led.split_transaction(
            t.id,
            [
                (-333, ("Food & Drink", "Groceries")),
                (-333, ("Shopping", "Online")),
                (None, ("Shopping", "Clothing")),
            ],
        )
        b = led.category_breakdown(JAN)
        s = led.summarise_month(JAN)
        assert s.money_out == Decimal("3.33")
        assert b.spending_after_refunds + b.refunds + b.fees == s.money_out
        assert sum(c.amount for c in b.categories) == Decimal("3.33")


def test_a_foreign_accounts_balance_counts_in_gbp_with_its_rate(
    led: Ledger, tmp_path: Path
) -> None:
    load(led, tmp_path, [*EXCHANGE, CAFE])
    b = led.balances_in_gbp()
    eur, gbp = sorted(led.accounts(), key=lambda a: a.currency)
    assert (eur.currency, gbp.currency) == ("EUR", "GBP")
    (c,) = [x for x in b.accounts if x.account_id == eur.id]
    assert c.conversion is not None
    assert c.conversion.amount_minor == 100_000 + 11500 - 2300
    assert c.conversion.gbp_minor == 94957  # 1092.00 EUR at 100/115
    assert c.conversion.rate.on == d(1)
    assert b.total == Decimal("1849.57")  # 900.00 GBP + 949.57


def test_a_balance_with_no_rate_leaves_the_total_unknown(
    led: Ledger, tmp_path: Path
) -> None:
    load(led, tmp_path, [CAFE])
    b = led.balances_in_gbp()
    assert b.total is None
    assert [x.conversion for x in b.accounts] == [None]


# --- properties: one rate per conversion, totals are their sum -------------

legs = st.lists(
    st.tuples(
        st.sampled_from(["EUR", "USD", "GBP"]),
        st.integers(1, 28),
        st.integers(-50_000, 50_000).filter(bool),
    ),
    max_size=12,
)


@settings(max_examples=40, deadline=None)
@given(
    legs,
    st.lists(st.tuples(st.integers(1, 28), st.integers(1, 5000)), max_size=4),
    st.lists(
        st.tuples(st.sampled_from(["EUR", "USD"]), st.integers(1, 28)), max_size=3
    ),
)
def test_totals_use_exactly_one_recorded_rate_per_conversion(
    spends: list[tuple[str, int, int]],
    exchanges: list[tuple[int, int]],
    entered: list[tuple[str, int]],
) -> None:
    rows: list[tuple[str, str, date, str, int]] = []
    for day, eur in exchanges:
        rows += [
            ("EXCHANGE", "GBP", d(day), "Exchanged to EUR", -eur),
            ("EXCHANGE", "EUR", d(day), "Exchanged from GBP", eur + eur // 7),
        ]
    rows += [("CARD_PAYMENT", c, d(day), "SYNTH", a) for c, day, a in spends]
    rows.sort(key=lambda r: r[2])
    with tempfile.TemporaryDirectory() as tmp:
        engine = open_db(Path(tmp) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            for cur, day in entered:
                led.set_rate(cur, Decimal("0.75"), d(day))
            if rows:
                load(led, Path(tmp), rows)
            s = led.summarise_month(JAN)
            fx = led.fx_month(JAN)
            foreign = [t for t in led.transactions() if t.currency != "GBP"]
            # every foreign Transaction is converted once or is rate needed
            assert len(fx.conversions) + len(fx.rate_needed) == len(foreign)
            for c in fx.conversions:
                assert c.rate.source in SOURCES and c.rate.gbp_per_unit > 0
                assert c.currency != "GBP" and c.rate.currency == c.currency
            got = {
                t.id: int(t.amount.scaleb(2))
                for t in led.transactions()
                if t.currency == "GBP"
            } | {c.transaction_id: c.gbp_minor for c in fx.conversions}
            ins = sum(v for v in got.values() if v > 0)
            outs = -sum(v for v in got.values() if v < 0)
            assert s.money_in + s.transfers_in == Decimal(ins).scaleb(-2)
            assert s.money_out + s.transfers_out == Decimal(outs).scaleb(-2)
        engine.dispose()
