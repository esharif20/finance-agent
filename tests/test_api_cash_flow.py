"""The what-if endpoint (#151): user-scoped, refuses bad input, and says
"insufficient" rather than guess. Synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.engine.forecast import cash_flow
from finance_agent.ledger import Ledger
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


FORECAST = "/api/cash-flow/forecast"


def test_the_forecast_band_matches_the_engine_exactly(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 1, 14)
    with engine.begin() as conn:
        flow = cash_flow(Ledger(conn, 1), date.today(), 3)
    want = [
        {
            "month": m.month,
            "point": m.total.point,
            "lower": m.total.lower,
            "upper": m.total.upper,
        }
        for m in flow.months
    ]
    with TestClient(api.create_app(engine)) as c:
        got = c.get(FORECAST).json()
    assert want and got == want
    assert all(type(v) is int for row in got for k, v in row.items() if k != "month")


def test_the_forecast_never_shows_another_users_history(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    _load(engine, write_export, 2, 14)  # someone else's full history
    with TestClient(api.create_app(engine)) as c:
        assert c.get(FORECAST).json() == []


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
