"""The monthly money story's structured recap (#266, decision 8 of #251).

A month's recap holds every figure the story may state, all integer pence
from the Engine (ADR 0007); #276 only words it.

- **Changes**: the Categories whose spend that month moved furthest from the
  personal baseline (`engine.baselines.seasonal_baseline`, ADR 0015: a
  Category without six months of history is left out, never estimated), by
  at least `MATERIAL` of it and `MIN_CHANGE` pence, at most `MAX_CHANGES`.
- **Causes**: for each change, the Merchants that drove it. A Merchant's
  change is its month against its own baseline, over the same months as its
  Category's. The one that moved most the same way is named, and any other
  whose change is at least `SHARE` of the Category's, at most `MAX_DRIVERS`.
  Medians do not add, so the drivers' changes need not sum to the
  Category's.
- **Anomalies**: that month's Insights, as `engine.insights.found` raises them
  from the rows up to the month's end.
- Life changes join once #270 stores them.

Spend is Spending net of Refunds, never below zero, from `engine.insights.rows`
(GBP pence; a foreign row still needing a rate is left out).

The fingerprint hashes every row the recap rests on (all rows up to the
month's end, as the baseline reads earlier months), the stopped Merchants and
`VERSION`, so equal fingerprints give equal recaps. `story` recomputes the
figures on every read and returns the stored wording only while the
fingerprint is unchanged (decision 8): a stale figure is never served.
"""

import calendar
import hashlib
import json
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection

from finance_agent.engine import insights
from finance_agent.engine.baselines import Insufficient, seasonal_baseline
from finance_agent.engine.detectors import Row
from finance_agent.ledger import Ledger
from finance_agent.ledger.insights import Found
from finance_agent.ledger.stories import Stories

VERSION = 1  # bump when the recap's rules change: old wording is then stale
MATERIAL = (1, 4)  # a change of at least a quarter of the baseline
MIN_CHANGE = 1_000  # and at least £10
MAX_CHANGES = 5
MAX_DRIVERS = 3
SHARE = (2, 5)  # a Merchant beyond the largest is named at 40% of the change
_MONTH = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class Driver:
    merchant: str
    spent: int  # pence out at this Merchant in the month, net of Refunds
    expected: int  # its baseline over the Category's baseline months
    change: int  # spent - expected


@dataclass(frozen=True)
class Change:
    category: str  # "Top / Sub"
    spent: int  # pence out in the month, net of Refunds, never below 0
    expected: int  # the personal baseline
    change: int  # spent - expected
    seasonal: bool  # the baseline follows the same month in earlier years
    drivers: tuple[Driver, ...]


@dataclass(frozen=True)
class Recap:
    month: str  # YYYY-MM
    changes: tuple[Change, ...]  # largest absolute change first
    anomalies: tuple[Found, ...]  # that month's Insights, by day raised

    def to_json(self) -> dict[str, Any]:
        out = asdict(self)
        for a in out["anomalies"]:
            a["raised_on"] = a["raised_on"].isoformat()
            a["evidence"] = list(a["evidence"])
        for c in out["changes"]:
            c["drivers"] = list(c["drivers"])
        out["changes"] = list(out["changes"])
        out["anomalies"] = list(out["anomalies"])
        return out


@dataclass(frozen=True)
class Story:
    recap: Recap  # always recomputed from the Ledger
    fingerprint: str
    wording: str | None  # stored wording, only while the fingerprint matches


def bounds(month: str) -> tuple[date, date]:
    """The first and last day of a YYYY-MM month."""
    if not _MONTH.fullmatch(month):
        raise ValueError("not a month (YYYY-MM)")
    y, m = int(month[:4]), int(month[5:7])
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _spent(rows: Iterable[tuple[date, int]], first: date, last: date) -> int:
    return max(0, -sum(a for d, a in rows if first <= d <= last))


def _change(
    pairs: list[tuple[date, int]], first: date, last: date
) -> tuple[int, int, bool] | None:
    b = seasonal_baseline(pairs, first)
    if isinstance(b, Insufficient):
        return None
    spent = _spent(pairs, first, last)
    return spent, b.expected, b.seasonal


def _drivers(
    rows: Sequence[Row], since: date, first: date, last: date, change: int
) -> tuple[Driver, ...]:
    by_merchant: dict[str, list[tuple[date, int]]] = {}
    for r in rows:
        if r.merchant:  # "" is no Merchant
            by_merchant.setdefault(r.merchant, []).append((r.posted_on, r.amount))
    found: list[Driver] = []
    for merchant, pairs in sorted(by_merchant.items()):
        # a month without this Merchant is a month of spending nothing at it,
        # back to the Category's first row: the same months as its baseline
        got = _change([(since, 0), *pairs], first, last)
        if got is not None:
            spent, expected, _ = got
            found.append(Driver(merchant, spent, expected, spent - expected))
    same = sorted(
        (d for d in found if d.change * change > 0),
        key=lambda d: (-abs(d.change), d.merchant),
    )
    big = [d for d in same[1:] if SHARE[1] * abs(d.change) >= SHARE[0] * abs(change)]
    return tuple([*same[:1], *big][:MAX_DRIVERS])


def recap(rows: Iterable[Row], month: str, stop: Collection[str] = ()) -> Recap:
    """`month`'s recap from the rows (`engine.insights.rows`); rows after the
    month are ignored, so rows dated after it never change it."""
    first, last = bounds(month)
    rows = [r for r in rows if r.posted_on <= last]
    by_category: dict[tuple[str, str], list[Row]] = {}
    for r in rows:
        by_category.setdefault(r.category, []).append(r)
    changes: list[Change] = []
    for category, rs in by_category.items():
        got = _change([(r.posted_on, r.amount) for r in rs], first, last)
        if got is None:
            continue
        spent, expected, seasonal = got
        delta = spent - expected
        if abs(delta) < MIN_CHANGE or MATERIAL[1] * abs(delta) < MATERIAL[0] * expected:
            continue
        since = min(r.posted_on for r in rs)
        drivers = _drivers(rs, since, first, last, delta)
        changes.append(
            Change(insights.label(category), spent, expected, delta, seasonal, drivers)
        )
    changes.sort(key=lambda c: (-abs(c.change), c.category))
    anomalies = sorted(
        (f for f in insights.found(rows, stop) if first <= f.raised_on <= last),
        key=lambda f: (f.raised_on, f.type, f.key),
    )
    return Recap(month, tuple(changes[:MAX_CHANGES]), tuple(anomalies))


def fingerprint(rows: Iterable[Row], month: str, stop: Collection[str] = ()) -> str:
    """The hash of what `recap` reads for `month`: equal hashes, equal recaps."""
    last = bounds(month)[1]
    body = json.dumps(
        [
            VERSION,
            month,
            sorted(stop),
            sorted(
                [r.id, r.posted_on.isoformat(), r.merchant, list(r.category), r.amount]
                for r in rows
                if r.posted_on <= last
            ),
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()


def _read(conn: Connection, user_id: int, month: str) -> tuple[Recap, str]:
    bounds(month)  # a malformed month fails before any read
    led = Ledger(conn, user_id)
    rows, stop = insights.rows(led), insights.stopped(led)
    return recap(rows, month, stop), fingerprint(rows, month, stop)


def story(conn: Connection, user_id: int, month: str) -> Story:
    """`month`'s recap, recomputed from the Ledger now, with the stored wording
    only while the month's fingerprint is unchanged. Never writes."""
    r, fp = _read(conn, user_id, month)
    stored = Stories(conn, user_id).get(month)
    wording = stored.wording if stored and stored.fingerprint == fp else None
    return Story(r, fp, wording)


def save(
    conn: Connection, user_id: int, grounded: Story, wording: str | None = None
) -> bool:
    """Store `grounded`'s recap with its fingerprint and the wording written
    for it (#276). Refused (False) once the month's data has changed since,
    so wording is never stored against figures it was not grounded on."""
    r, fp = _read(conn, user_id, grounded.recap.month)
    if fp != grounded.fingerprint:
        return False
    Stories(conn, user_id).put(r.month, fp, r.to_json(), wording)
    return True
