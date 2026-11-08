"""Briefing topics from spending (#309), on the Mac, by code: no model.

`rank` takes the Ledger's Spend trees for the last `MONTHS` months (about 90
days) and turns them into public topics: a Subcategory maps to a fixed topic
("Energy" -> "UK energy prices"), a recognised catalogue service is its own
topic ("Netflix"). Topics rank by what was spent on them; the top `TOP` are
suggested. Nothing leaves from here: the user ticks which topics may go
(`storage.briefing`), and `gateway.brief.payload` sends only names.

`spent_minor` is the user's own figure for the "why it matters" line, filled
in locally and never sent.

`news_for` hands the advisor (#308) the stored items for a service or group,
quoted data only, newest first, none older than `gateway.brief.STALE_DAYS`."""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy import Connection

from finance_agent.categorise.subscriptions import known_service
from finance_agent.engine.subscription_budget import NewsItem
from finance_agent.gateway.types import TOPIC
from finance_agent.ledger import Ledger, Spend

MONTHS = 3
TOP = 8

# (Category, Subcategory) -> a public topic. Unlisted ones (Rent, Taxi, ...)
# have no news worth a daily search, so they suggest nothing.
SUBCATEGORY_TOPIC: dict[tuple[str, str], str] = {
    ("Bills", "Energy"): "UK energy prices",
    ("Bills", "Water"): "UK water bills",
    ("Bills", "Council tax"): "UK council tax",
    ("Bills", "Phone & broadband"): "UK broadband and mobile prices",
    ("Bills", "Insurance"): "UK insurance prices",
    ("Bills", "Rent or mortgage"): "UK mortgage rates",
    ("Transport", "Public transport"): "UK train fares",
    ("Transport", "Fuel"): "UK fuel prices",
    ("Food & Drink", "Groceries"): "UK food prices",
    ("Food & Drink", "Takeaway"): "Food delivery apps",
    ("Subscriptions", "AI tools"): "AI assistants",
    ("Subscriptions", "Streaming"): "Streaming services",
    ("Subscriptions", "Software"): "Software subscriptions",
    ("Subscriptions", "Cloud storage"): "Cloud storage plans",
    ("Health", "Fitness"): "Gym memberships",
    ("Travel", "Flights"): "Flight prices",
    ("Entertainment", "Games"): "Video games",
}
# the Subscriptions group's topic, for the advisor's news
GROUP_TOPIC = {
    sub: t for (top, sub), t in SUBCATEGORY_TOPIC.items() if top == "Subscriptions"
}


@dataclass(frozen=True)
class Topic:
    name: str
    kind: Literal["category", "service"]
    spent_minor: int  # pence over the window; local only, never sent


def months_before(today: date, n: int = MONTHS) -> list[str]:
    """`n` months up to and including today's, as YYYY-MM."""
    y, m = today.year, today.month
    out: list[str] = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def _minor(s: Spend) -> int:
    return int(s.amount.scaleb(2))


def rank(months: Iterable[Sequence[Spend]], top: int = TOP) -> list[Topic]:
    """Topics by spend, largest first, ties by name; only those spent on."""
    totals: dict[tuple[str, Literal["category", "service"]], int] = {}
    for tree in months:
        for cat in tree:
            for sub in cat.children:
                topic = SUBCATEGORY_TOPIC.get((cat.name, sub.name))
                if topic:
                    k = (topic, "category")
                    totals[k] = totals.get(k, 0) + _minor(sub)
                for merchant in sub.children:
                    known = known_service(merchant.name)
                    if known and re.fullmatch(TOPIC, known[0]):
                        k = (known[0], "service")
                        totals[k] = totals.get(k, 0) + _minor(merchant)
    ranked = sorted(
        (Topic(n, kind, v) for (n, kind), v in totals.items() if v > 0),
        key=lambda t: (-t.spent_minor, t.name),
    )
    return ranked[:top]


def for_ledger(led: Ledger, today: date) -> list[Topic]:
    return rank(led.spending_by_category(m) for m in months_before(today))


State = Literal["on", "off", "suggested"]


def listing(
    suggested: Sequence[Topic], prefs: Mapping[str, str]
) -> list[tuple[str, State, Topic | None]]:
    """Every topic Settings shows: the suggestions in rank order, then the
    ones the user added. A suggestion the user never ticked stays `suggested`
    and is not sent."""
    by_lower = {k.lower(): (k, v) for k, v in prefs.items()}
    out: list[tuple[str, State, Topic | None]] = []
    for t in suggested:
        state = by_lower.pop(t.name.lower(), (t.name, "suggested"))[1]
        out.append((t.name, state if state in ("on", "off") else "suggested", t))  # pyright: ignore[reportArgumentType]
    for name, state in by_lower.values():
        out.append((name, "off" if state == "off" else "on", None))
    return out


def to_send(suggested: Sequence[Topic], prefs: Mapping[str, str]) -> list[str]:
    """The ticked topics, ranked ones first: the only names that may leave."""
    return [n for n, state, _ in listing(suggested, prefs) if state == "on"]


def db_path(conn: Connection) -> Path | None:
    """The Ledger file the briefing store sits beside; None in memory."""
    db = conn.engine.url.database
    return Path(db) if db and db != ":memory:" else None


def news_for(
    conn: Connection, user_id: int, today: date, names: Iterable[str]
) -> Mapping[str, tuple[NewsItem, ...]]:
    """Stored items by lower-cased topic, for the topics in `names`."""
    from finance_agent.storage.briefing import BriefingStore  # engine reads it only

    path = db_path(conn)
    if path is None or not BriefingStore.exists_beside(path):
        return {}
    want = {n.lower() for n in names}
    out: dict[str, list[NewsItem]] = {}
    for it in BriefingStore.beside(path).items(user_id, today):
        if it.topic.lower() in want:
            out.setdefault(it.topic.lower(), []).append(
                NewsItem(it.on, it.change, it.summary, tuple(s.url for s in it.sources))
            )
    return {k: tuple(v) for k, v in out.items()}
