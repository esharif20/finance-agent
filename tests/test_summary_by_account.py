"""`summarise_month_by_account` (one pass, for /api/summary) gives exactly the
figures of `summarise_month` called per Account, and its parts add up."""

import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_banks import hsbc_export, revolut_pockets
from synthetic_lloyds import generate_histories, render_export
from synthetic_simplefin import response

START = date(2026, 1, 1)


@st.composite
def ledgers(draw: st.DrawFn) -> list[str]:
    a, b = generate_histories(
        seed=draw(st.integers(0, 10_000)), days=draw(st.integers(8, 70))
    )
    texts = [render_export(h, 0, min(len(h.txns), 150)).text for h in (a, b)]
    if draw(st.booleans()):  # a balance-less Bank
        texts.append(
            hsbc_export(draw(st.integers(0, 10_000)), draw(st.integers(1, 30)))
        )
    rows: list[tuple[str, str, date, str, int]] = []
    for i in range(draw(st.integers(0, 6))):  # EUR with or without an exchange rate
        day = START + timedelta(days=draw(st.integers(0, 70)))
        if draw(st.booleans()):
            gbp = draw(st.integers(1000, 20000))
            rows += [
                ("EXCHANGE", "GBP", day, "Exchanged to EUR", -gbp),
                ("EXCHANGE", "EUR", day, "Exchanged from GBP", gbp * 115 // 100),
            ]
        rows.append(
            ("CARD_PAYMENT", "EUR", day, f"SYNTH CAFE {i}", -draw(st.integers(1, 9000)))
        )
    if rows:
        texts.append(revolut_pockets(sorted(rows, key=lambda r: r[2])))
    return texts


@settings(max_examples=20, deadline=None)
@given(ledgers())
def test_one_pass_equals_the_per_call_path_and_parts_add_up(texts: list[str]) -> None:
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        paths: list[Path] = []
        for i, text in enumerate(texts):
            p = Path(d) / f"export{i}.csv"
            p.write_text(text)
            paths.append(p)
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            led.import_files(paths)
            ids = led.account_ids()
            assert ids == [a.id for a in led.accounts()]
            for month in led.months():
                whole, parts = led.summarise_month_by_account(month, ids)
                assert whole == led.summarise_month(month)
                assert parts == {a: led.summarise_month(month, a) for a in ids}
                for f in ("money_in", "money_out", "transfers_in", "transfers_out"):
                    assert sum(
                        (getattr(p, f) for p in parts.values()), Decimal(0)
                    ) == getattr(whole, f)
                assert sum(p.rate_needed for p in parts.values()) == whole.rate_needed
        engine.dispose()


def test_synced_accounts_are_listed_and_summarised_as_accounts_lists_them(
    ledger_for: Callable[[int], Ledger], write_export: Callable[[str], Path]
) -> None:
    h = generate_histories(7, 40)[0]
    led = ledger_for(1)
    led.import_files([write_export(render_export(h, 0, 30).text)])
    led.apply_sync(response(h, 0, 6, account_id="ACC-2"))  # synced only
    led.apply_sync(response(h, 5, 20, pending=2))
    ids = led.account_ids()
    assert ids == [a.id for a in led.accounts()] and len(ids) >= 2
    assert ledger_for(2).account_ids() == []  # another user's are never listed
    for month in led.months():
        whole, parts = led.summarise_month_by_account(month, ids)
        assert whole == led.summarise_month(month)
        assert parts == {a: led.summarise_month(month, a) for a in ids}
