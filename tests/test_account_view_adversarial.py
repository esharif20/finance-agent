"""Verifier's adversarial tests for #59: multi-Account histories with linked,
unlinked and cross-month Transfers plus Refunds, and another user's Account id.

Amounts are chosen by residue so only the generated pairs can link: pair legs
end in 0 pence, lone transfer legs in 1, purchases in 3, refunds in 5, income
in 7."""

import tempfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import History, Txn, render_export
from synthetic_lloyds.generator import SORT_CODE, START

Event = tuple[int, str, str, int]  # day, Bank type, description, signed pence


def _history(events: list[Event], number: str) -> History:
    out: list[Txn] = []
    bal = 500000
    for day, typ, desc, amt in sorted(events, key=lambda e: e[0]):
        bal += amt
        out.append(Txn(START + timedelta(days=day), typ, desc, amt, bal))
    return History(SORT_CODE, number, out)


@st.composite
def books(draw: st.DrawFn) -> tuple[list[list[Event]], list[tuple[int, int]], set[int]]:
    """Events per Account, the (from, to) Account of each pair, and which
    Accounts get imported."""
    n = draw(st.integers(2, 3))
    acct = st.integers(0, n - 1)
    day = st.integers(0, 70)
    events: list[list[Event]] = [[] for _ in range(n)]
    pairs: list[tuple[int, int]] = []
    for k in range(draw(st.integers(0, 5))):
        a, b = draw(acct), draw(acct)
        if a == b:
            continue
        d, lag = draw(day), draw(st.integers(0, 3))  # lag may cross a month end
        amt = 10000 + k * 1000
        events[a].append((d, "TFR", "OWN MOVE", -amt))
        events[b].append((d + lag, "TFR", "OWN MOVE", amt))
        pairs.append((a, b))
    for k in range(draw(st.integers(0, 3))):  # other leg outside the Ledger
        sign = draw(st.sampled_from([-1, 1]))
        events[draw(acct)].append(
            (draw(day), "TFR", "SYNTH SAVER", sign * (2001 + k * 10))
        )
    for k in range(draw(st.integers(1, 8))):
        a, d = draw(acct), draw(day)
        events[a].append((d, "DEB", "TESCO STORES 3021", -(3003 + k * 10)))
        if draw(st.booleans()):  # refunded, maybe in a later month
            events[a].append(
                (d + draw(st.integers(0, 40)), "DEB", "TESCO REFUND", 3005 + k * 10)
            )
    for k in range(draw(st.integers(0, 3))):
        events[draw(acct)].append(
            (draw(day), "FPI", "ACME LTD SALARY", 150007 + k * 10)
        )
    imported = draw(st.sets(st.integers(0, n - 1), min_size=1))
    return events, pairs, imported


def _minor(m: dict[str, Any]) -> int:
    return int(m["minor"])


@settings(max_examples=25, deadline=None)
@given(books())
def test_combined_is_the_accounts_summed_net_of_linked_transfers_only(
    book: tuple[list[list[Event]], list[tuple[int, int]], set[int]],
) -> None:
    events, pairs, imported = book
    with tempfile.TemporaryDirectory() as d:
        paths: list[Path] = []
        for i in sorted(imported):
            h = _history(events[i], f"0000000{i + 1}")
            if not h.txns:
                continue
            p = Path(d) / f"e{i}.csv"
            p.write_text(render_export(h, 0, len(h.txns)).text)
            paths.append(p)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            if paths:
                ledger.import_files(paths)
            stored = ledger.transactions()
        # only legs of pairs with both Accounts imported may link
        expected_pairs = sum(1 for a, b in pairs if {a, b} <= imported)
        linked = {t.id for t in stored if t.transfer_with is not None}
        assert len(linked) == 2 * expected_pairs
        assert all(int(t.amount.scaleb(2)) % 10 == 0 for t in stored if t.id in linked)
        with TestClient(api.create_app(engine)) as c:
            accounts = c.get("/api/accounts").json()
            ids = [a["id"] for a in accounts]
            months = sorted({t.posted_on.strftime("%Y-%m") for t in stored})
            for m in months:
                whole = c.get("/api/summary", params={"month": m}).json()
                rows = [t for t in stored if t.posted_on.strftime("%Y-%m") == m]

                def pence(
                    acct: int | None, moved: bool, sign: int, rows: list[Any] = rows
                ) -> int:
                    return sum(
                        abs(int(t.amount.scaleb(2)))
                        for t in rows
                        if (acct is None or t.account_id == acct)
                        and (t.id in linked) == moved
                        and (t.amount > 0) == (sign > 0)
                    )

                # unlinked Transfers, Refunds and income are never dropped
                assert _minor(whole["money_in"]) == pence(None, False, 1)
                assert _minor(whole["money_out"]) == pence(None, False, -1)
                assert _minor(whole["transfers_in"]) == pence(None, True, 1)
                assert _minor(whole["transfers_out"]) == pence(None, True, -1)
                assert _minor(whole["net"]) == pence(None, False, 1) - pence(
                    None, False, -1
                )
                parts = {p["account_id"]: p for p in whole["accounts"]}
                assert set(parts) == set(ids)
                for key in ("money_in", "money_out", "transfers_in", "transfers_out"):
                    assert sum(_minor(p[key]) for p in parts.values()) == _minor(
                        whole[key]
                    )
                for i in ids:
                    one = c.get("/api/summary", params={"month": m, "account_id": i})
                    body = one.json()
                    assert _minor(body["money_in"]) == pence(i, False, 1)
                    assert _minor(body["money_out"]) == pence(i, False, -1)
                    assert _minor(body["transfers_in"]) == pence(i, True, 1)
                    assert _minor(body["transfers_out"]) == pence(i, True, -1)
                    for key in (
                        "money_in",
                        "money_out",
                        "transfers_in",
                        "transfers_out",
                    ):
                        assert body[key] == parts[i][key]
                    series = c.get(
                        "/api/month-series", params={"account_id": i, "limit": 24}
                    ).json()
                    row = next(s for s in series if s["month"] == m)
                    assert row["money_in"] == body["money_in"]
                    assert row["money_out"] == body["money_out"]
        engine.dispose()


def test_another_users_account_id_is_404_and_never_data(
    engine: Engine, write_export: Callable[[str], Path]
) -> None:
    mine = _history([(0, "DEB", "TESCO STORES 3021", -3003)], "00000001")
    theirs = _history(
        [(0, "DEB", "TESCO STORES 3021", -9993), (1, "FPI", "ACME LTD SALARY", 50007)],
        "00000009",
    )
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([write_export(render_export(mine, 0, 1).text)])
        other = Ledger(conn, 2)
        other.import_files([write_export(render_export(theirs, 0, 2).text)])
        (foreign,) = [a.id for a in other.accounts()]
    with TestClient(api.create_app(engine)) as c:
        assert foreign not in [a["id"] for a in c.get("/api/accounts").json()]
        p = {"month": "2026-01"}
        for bad in (foreign, -1, 0, 2**62):
            q = {**p, "account_id": bad}
            for url in ("/api/summary", "/api/spending", "/api/month-series"):
                assert c.get(url, params=q).status_code == 404, (url, bad)
            r = c.get("/api/categories/Groceries", params=q)
            assert r.status_code == 404
            rows = c.get("/api/transactions", params=q)
            assert rows.status_code in (200, 404)
            if rows.status_code == 200:
                assert rows.json() == [] or rows.json().get("items") == []
        for junk in ("abc", "1.5", ""):
            r = c.get("/api/summary", params={**p, "account_id": junk})
            assert r.status_code == 422
        whole = c.get("/api/summary", params=p).json()
        assert _minor(whole["money_out"]) == 3003  # user 2's rows never leak in
