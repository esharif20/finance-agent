"""Confirmed life changes (#270, decision 5 of #251), scoped to one user.

A detected level shift (`engine.changepoint`) is only a Review Queue question,
"Did something change in <month>?". Only the user's confirmation writes a row
here; from its month, `engine.baselines` and `engine.forecast` read only that
series' data. Undo deletes the row, which restores the earlier baselines.

A series is a set of the Ledger's Transactions (`series_of`): income is every
Income credit, rent is the rent-or-mortgage Category, bills the rest of Bills,
groceries the Groceries Category. Nothing here detects or computes a figure.

Nothing here logs; amounts never leave the database.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Literal

from sqlalchemy import Connection

from finance_agent.ledger import Ledger, Transaction
from finance_agent.ledger.fx import GBP
from finance_agent.ledger.taxonomy import current
from finance_agent.storage import Scoped, schema

type Series = Literal["income", "rent", "bills", "groceries"]
SERIES: tuple[Series, ...] = ("income", "rent", "bills", "groceries")
RENT = ("Bills", "Rent or mortgage")
GROCERIES = ("Food & Drink", "Groceries")
_L = schema.life_change
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class Detected:
    """A shift the Engine found: `series` moved from `before` to `after`
    (monthly medians, pence) from `month`, the first month at the new level."""

    series: Series
    month: date
    before: int
    after: int
    evidence: tuple[int, ...] = ()  # Transaction ids


def series_of(
    kind: str, category: tuple[str, str] | None, taxonomy_version: int
) -> Series | None:
    """The series a categorised Transaction belongs to, if any. A Refund
    counts against its Category, as the Ledger nets a Category's spend."""
    if kind == "Income":
        return "income"
    if kind not in ("Spending", "Refund") or category is None:
        return None
    cat = current(taxonomy_version, category)  # older names map forward
    if cat == RENT:
        return "rent"
    if cat[0] == "Bills":
        return "bills"
    return "groceries" if cat == GROCERIES else None


def series_rows(
    txns: Iterable[Transaction], series: Series
) -> list[tuple[int, date, int]]:
    """(id, posted on, signed pence) of `series`, GBP rows only.
    shortcut: a foreign row is left out rather than converted; use
    `forecast.gbp_rows` first if a foreign salary or rent ever matters."""
    out: list[tuple[int, date, int]] = []
    for t in txns:
        c = t.categorisation
        if c is None or t.currency != GBP:
            continue
        cat = (c.category[0], c.category[1]) if c.category else None
        if series_of(c.kind, cat, c.taxonomy_version) == series:
            out.append((t.id, t.posted_on, int(t.amount.scaleb(2))))
    return out


def month_key(d: date) -> str:
    return f"{d:%Y-%m}"


class LifeChanges:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    @classmethod
    def of(cls, led: Ledger) -> "LifeChanges":
        db = led._db  # pyright: ignore[reportPrivateUsage] - same package
        return cls(db.conn, db.user_id)

    def confirm(self, change: Detected) -> int:
        """Store a confirmed change; returns its row id (the undo handle)."""
        if change.series not in SERIES:
            raise ValueError("not a series")
        return self._db.insert(
            _L,
            series=change.series,
            month=month_key(change.month),
            before_minor=change.before,
            after_minor=change.after,
            created_at=datetime.now(UTC),
        )

    def remove(self, change_id: int) -> bool:
        """Delete one confirmation (undo); False if it was already gone."""
        res = self._db.execute(self._db.delete(_L).where(_L.c.id == change_id))
        return res.rowcount > 0

    def confirmed(self) -> list[tuple[Series, str]]:
        """(series, YYYY-MM) of every confirmation, oldest month first."""
        rows = self._db.execute(
            self._db.select(_L, _L.c.series, _L.c.month).order_by(_L.c.month, _L.c.id)
        )
        return [(r.series, r.month) for r in rows]

    def resets(self) -> dict[Series, date]:
        """Each series' latest confirmed change month: its data starts there."""
        out: dict[Series, date] = {}
        for series, month in self.confirmed():
            if _MONTH.fullmatch(month):
                out[series] = date(int(month[:4]), int(month[5:]), 1)
        return out

    def forget(self) -> int:
        """Delete every confirmation of this user."""
        return self._db.execute(self._db.delete(_L)).rowcount
