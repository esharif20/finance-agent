"""Synthetic Ledgers for the Assistant's Proposal, graph and cache tests: a few
known Transactions per user, imported and categorised. Synthetic only."""

from datetime import timedelta
from pathlib import Path

from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.ledger import Ledger
from synthetic_lloyds import History, Txn, render_export
from synthetic_lloyds.generator import SORT_CODE, START

ME, OTHER = 1, 2  # current_user() is 1
GROCERIES = ("Food & Drink", "Groceries")
EATING_OUT = ("Food & Drink", "Eating out")

# (day after START, Lloyds type, description, signed pence)
ROWS = [
    (1, "FPI", "ACME LTD SALARY", 250000),
    (2, "DEB", "TESCO STORES 3214", -4250),
    (3, "DEB", "PRET A MANGER 0231", -675),
    (4, "DEB", "DELIVEROO 2BX9", -2310),
    (5, "DEB", "TESCO STORES 3214", -1999),
]
# December 2025's Transport, for the Subcategory golden cases: £112.80 in all,
# Fuel £84.00, Taxi £16.30, Public transport £12.50
DECEMBER = [
    (-25, "DEB", "SHELL KINGS CROSS", -4800),
    (-20, "DEB", "TFL TRAVEL CH", -1250),
    (-14, "DEB", "UBER TRIP", -1630),
    (-10, "DEB", "TESCO PFS 4471", -3600),
]


def history(rows: list[tuple[int, str, str, int]], account: str) -> History:
    out: list[Txn] = []
    bal = 100000
    for day, typ, desc, amt in rows:
        bal += amt
        out.append(Txn(START + timedelta(days=day), typ, desc, amt, bal))
    return History(SORT_CODE, account, out)


def seed(
    engine: Engine,
    user: int,
    d: Path,
    rows: list[tuple[int, str, str, int]] = ROWS,
) -> dict[str, int]:
    """Import `rows` for `user`; returns {description: Transaction id} (the
    last one wins for a repeated description)."""
    h = history(rows, f"0000000{user}")
    path = d / f"assistant-u{user}-{len(rows)}.csv"
    path.write_text(render_export(h, 0, len(rows)).text)
    with engine.begin() as conn:
        led = Ledger(conn, user)
        led.import_files([path])
        categorise.run(conn, user)
        return {t.description: t.id for t in led.transactions()}
