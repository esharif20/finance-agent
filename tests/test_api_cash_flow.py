"""The what-if endpoint (#151): user-scoped, refuses bad input, and says
"insufficient" rather than guess. Synthetic data only."""

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine, update

from finance_agent import api, categorise
from finance_agent.ingest.simplefin import LONDON
from finance_agent.ledger import Ledger
from finance_agent.storage import schema
from synthetic_banks import revolut_pockets
from synthetic_lloyds import History, Txn, render_export
from synthetic_lloyds.generator import SORT_CODE

URL = "/api/cash-flow/simulate-purchase"


def _month(back: int, day: int) -> date:
    t = date.today().replace(day=1)
    y, m = divmod(t.year * 12 + t.month - 1 - back, 12)
    return date(y, m + 1, day)


def _history(months: int, number: str) -> History:
    """Salary on the 25th and groceries on the 3rd, 10th and 17th, from a
    £5,000 balance (a Lloyds export, so the Bank's balance is known)."""
    rows: list[tuple[date, str, str, int]] = []
    for back in range(months, 0, -1):
        rows.append((_month(back, 25), "FPI", "SYNTH ACME LTD SALARY", 210_000))
        for day in (3, 10, 17):
            rows.append((_month(back, day), "DEB", "SYNTH GROCER", -15_000 - back % 4))
    out: list[Txn] = []
    bal = 500_000
    for d, typ, desc, amt in sorted(rows):
        bal += amt
        out.append(Txn(d, typ, desc, amt, bal))
    return History(SORT_CODE, number, out)


def _load(engine: Engine, write: Callable[[str], Path], user: int, months: int) -> None:
    h = _history(months, f"0000000{user}")
    with engine.begin() as conn:
        Ledger(conn, user).import_files([write(render_export(h, 0, len(h.txns)).text)])
        categorise.run(conn, user)


def test_a_purchase_is_simulated_on_the_users_own_forecast(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 1, 14)
    with TestClient(api.create_app(engine)) as c:
        small = c.get(URL, params={"amount_minor": 100}).json()
        big = c.get(URL, params={"amount_minor": 10**12}).json()
    assert small["status"] == big["status"] == "ok", small
    assert big["risk"] == "below_zero"
    assert big["minimum"]["minor"] < small["minimum"]["minor"]
    assert big["floor"]["minor"] <= big["minimum"]["minor"]
    assert date.fromisoformat(small["payday"]) > date.today()


def test_bad_amounts_and_past_dates_are_refused(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 1, 14)
    yesterday = date.today() - timedelta(days=1)
    with TestClient(api.create_app(engine)) as c:
        for amount in (0, -1, 10**13 + 1, 2**63, "1.5", "abc", ""):
            r = c.get(URL, params={"amount_minor": amount})
            assert r.status_code == 422, amount
        assert c.get(URL).status_code == 422
        r = c.get(URL, params={"amount_minor": 100, "on": yesterday.isoformat()})
        assert r.status_code == 422
        for buffer in (-1, 10**13 + 1):
            r = c.get(URL, params={"amount_minor": 100, "buffer_minor": buffer})
            assert r.status_code == 422


def test_too_little_history_is_insufficient(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 1, 4)  # under six pay cycles
    with TestClient(api.create_app(engine)) as c:
        body = c.get(URL, params={"amount_minor": 100}).json()
    assert body["status"] == "insufficient"
    assert body["minimum"] is None and body["risk"] is None
    assert body["have"] < body["need"]


def test_another_users_history_never_answers_for_this_one(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 2, 14)  # someone else's full history
    with TestClient(api.create_app(engine)) as c:
        assert c.get(URL, params={"amount_minor": 100}).status_code == 404
    _load(engine, write_export, 1, 4)
    with TestClient(api.create_app(engine)) as c:
        body = c.get(URL, params={"amount_minor": 100}).json()
    assert body["status"] == "insufficient"  # not user 2's forecast


def test_a_simulation_writes_nothing(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    """Verifier (#151): a what-if is a read. No row is written, whatever the
    amount (no payment, no Proposal, no cached Insight)."""
    from sqlalchemy import event

    _load(engine, write_export, 1, 14)
    writes: list[str] = []

    def spy(*args: object) -> None:
        sql = str(args[2]).lstrip().upper()
        if sql.startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(sql.split()[0])

    event.listen(engine, "before_cursor_execute", spy)
    try:
        with TestClient(api.create_app(engine)) as c:
            for amount in (1, 10**6, 10**13):
                r = c.get(URL, params={"amount_minor": amount})
                assert r.status_code == 200, r.text
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert writes == []


def test_the_projection_starts_from_the_last_import_and_says_so(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    """Verifier #227: the balance is as of the last import, so starting from
    today would skip the spend since then and look optimistic."""
    _load(engine, write_export, 1, 14)
    with TestClient(api.create_app(engine)) as c:
        fresh = c.get(URL, params={"amount_minor": 100}).json()
    stale = date.today() - timedelta(days=10)
    si = schema.statement_import
    with engine.begin() as conn:
        conn.execute(
            update(si).values(imported_at=datetime.combine(stale, time(9), UTC))
        )
    with TestClient(api.create_app(engine)) as c:
        old = c.get(URL, params={"amount_minor": 100}).json()
    assert fresh["as_of"] == datetime.now(LONDON).date().isoformat()
    assert old["status"] == "ok" and old["as_of"] == stale.isoformat()


def test_a_purchase_past_the_forecast_horizon_is_refused(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 1, 14)
    today = date.today()
    beyond = date(today.year + (today.month + 2) // 12, (today.month + 2) % 12 + 1, 1)
    with TestClient(api.create_app(engine)) as c:
        r = c.get(URL, params={"amount_minor": 100, "on": beyond.isoformat()})
        assert r.status_code == 422, r.text
        inside = beyond - timedelta(days=1)
        r = c.get(URL, params={"amount_minor": 100, "on": inside.isoformat()})
        assert r.status_code == 200, r.text


def test_a_foreign_spend_without_a_rate_never_moves_the_what_if(
    engine: Engine, write_export: Callable[[str], Path], tmp_path: Path
) -> None:
    _load(engine, write_export, 1, 14)
    with TestClient(api.create_app(engine)) as c:
        before = c.get(URL, params={"amount_minor": 100}).json()
    eur = [
        ("CARD_PAYMENT", "EUR", _month(back, 12), "SYNTH CAFE", -900_000)
        for back in range(1, 8)
    ]
    p = tmp_path / "revolut.csv"
    p.write_text(revolut_pockets(eur))
    with engine.begin() as conn:
        assert Ledger(conn, 1).import_files([p]).rejected == []
        categorise.run(conn, 1)
    with TestClient(api.create_app(engine)) as c:
        after = c.get(URL, params={"amount_minor": 100}).json()
    assert after == before


def test_another_users_stale_import_never_moves_this_ones_start(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    """Verifier #227: `as_of` comes from this user's own accounts only."""
    _load(engine, write_export, 1, 14)
    _load(engine, write_export, 2, 14)
    si = schema.statement_import
    old = datetime.combine(date.today() - timedelta(days=20), time(9), UTC)
    with engine.begin() as conn:
        conn.execute(update(si).where(si.c.user_id == 2).values(imported_at=old))
    with TestClient(api.create_app(engine)) as c:
        body = c.get(URL, params={"amount_minor": 100}).json()
    assert body["as_of"] == datetime.now(LONDON).date().isoformat()


def test_income_into_a_foreign_account_never_props_up_the_gbp_pool(
    engine: Engine, write_export: Callable[[str], Path], tmp_path: Path
) -> None:
    """Verifier #227: the pool is GBP accounts only, so a EUR salary (even at
    a rate) must not be projected into it; that would be optimistic."""
    _load(engine, write_export, 1, 14)
    with TestClient(api.create_app(engine)) as c:
        before = c.get(URL, params={"amount_minor": 100}).json()
    eur = [
        ("TOPUP", "EUR", _month(back, 20), "SYNTH EURO CO SALARY", 500_000)
        for back in range(1, 13)
    ]
    p = tmp_path / "revolut.csv"
    p.write_text(revolut_pockets(eur))
    with engine.begin() as conn:
        led = Ledger(conn, 1)
        assert led.import_files([p]).rejected == []
        led.set_rate("EUR", Decimal("0.85"), date(2020, 1, 1))
        categorise.run(conn, 1)
        kinds = {
            t.categorisation.kind
            for t in led.transactions()
            if t.currency == "EUR" and t.categorisation
        }
    assert kinds == {"Income"}, kinds  # the case under test is reached
    with TestClient(api.create_app(engine)) as c:
        after = c.get(URL, params={"amount_minor": 100}).json()
    assert after == before
