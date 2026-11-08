"""The daily briefing's gateway half (#309; ADR 0011, 0016): pure, no database.

`payload` is the only way topics leave: the user's ticked topic names, each
words only (`types.TOPIC`), mixed with 2 or 3 generic decoys and sorted, so
neither the order nor the count says what was spent. No amount, Merchant text
or ranking can ride on one.

`parse` reads the reply, which is untrusted web text gathered by Search
grounding on Google's side. It keeps only well-formed items for a topic that
was asked about (never a decoy), dated within `STALE_DAYS` and not in the
future, with https sources. Text is folded to plain characters and cut to
length; it is shown as quoted data with its citation and enters a prompt only
as `{"data": ...}` (`assistant.tools.read.Data` sanitises it there). Nothing
here can trigger an action or change a figure."""

import hashlib
import json
import random
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal, get_args
from urllib.parse import urlsplit

from finance_agent.gateway.types import TOPIC, BriefPayload

MAX_TOPICS = 8
STALE_DAYS = 30
# Generic, public and unrelated to any spending Category or catalogue service.
DECOYS = (
    "Electric cars",
    "Premier League",
    "Smartphone releases",
    "Space exploration",
    "UK weather",
    "Wimbledon",
    "Video game consoles",
    "Olympic Games",
    "Climate policy",
    "Museum exhibitions",
)
Change = Literal[
    "price_change", "plan_change", "outage", "release", "switching", "other"
]
_CHANGES = frozenset(get_args(Change))
MAX_HEADLINE = 140
MAX_SUMMARY = 480
MAX_TITLE = 120
MAX_URL = 500
MAX_SOURCES = 3
MAX_ITEMS = 40


class BriefError(ValueError):
    """The reply is not the JSON asked for. The message names no content."""


@dataclass(frozen=True)
class Source:
    url: str
    title: str


@dataclass(frozen=True)
class Item:
    topic: str  # as the user's topic is spelt
    headline: str
    summary: str
    change: Change
    on: date
    sources: tuple[Source, ...]

    @property
    def dedupe(self) -> str:
        """The same story on another day: its topic and normalised headline."""
        words = re.findall(r"[a-z0-9]+", f"{self.topic}\n{self.headline}".lower())
        return hashlib.sha256(" ".join(words).encode()).hexdigest()[:32]


def payload(topics: Iterable[str], rng: random.Random | None = None) -> BriefPayload:
    """The first `MAX_TOPICS` distinct topics (the caller's order: ranked) plus
    2 or 3 decoys none of them matches, sorted. Raises ValueError for a topic
    that is not words only, so nothing leaves."""
    rng = rng or random.SystemRandom()
    chosen: dict[str, str] = {}
    for t in topics:
        t = " ".join(t.split())
        if not re.fullmatch(TOPIC, t):
            raise ValueError("a topic is words only")
        chosen.setdefault(t.lower(), t)
    if not chosen:
        raise ValueError("no topics")
    real = list(chosen.values())[:MAX_TOPICS]
    pool = [d for d in DECOYS if d.lower() not in chosen]
    mixed = real + rng.sample(pool, rng.randint(2, 3))
    return BriefPayload(topics=tuple(sorted(mixed, key=str.lower)))


def _text(v: Any, limit: int) -> str:
    """Plain characters only: NFKC, no control, format (zero-width, bidi) or
    private-use characters, whitespace collapsed, cut to `limit`."""
    if not isinstance(v, str):
        raise BriefError("a field is not text")
    folded = unicodedata.normalize("NFKC", v)
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in folded
        if ch.isspace() or unicodedata.category(ch)[0] != "C"
    )
    out = " ".join(kept.split())[:limit].strip()
    if not out:
        raise BriefError("a field is empty")
    return out


def _url(v: Any) -> str:
    if not isinstance(v, str) or len(v) > MAX_URL or not v.isascii():
        raise BriefError("a source is not a URL")
    try:
        u = urlsplit(v)
        ok = u.scheme == "https" and bool(u.hostname) and not (u.username or u.password)
    except ValueError:
        ok = False
    if not ok or any(ch.isspace() or not ch.isprintable() for ch in v):
        raise BriefError("a source is not an https URL")
    return v


def _item(raw: Any, topics: dict[str, str], today: date) -> Item:
    if not isinstance(raw, dict):
        raise BriefError("an item is not an object")
    d: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    topic = topics.get(_text(d.get("topic"), 40).lower())
    if topic is None:
        raise BriefError("an item is for a topic not asked about, or a decoy")
    change = d.get("change")
    if change not in _CHANGES:
        change = "other"
    try:
        on = date.fromisoformat(str(d.get("date")))
    except ValueError:
        raise BriefError("an item has no date") from None
    if not today - timedelta(days=STALE_DAYS) <= on <= today:
        raise BriefError("an item is stale or in the future")
    raw_sources: Any = d.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise BriefError("an item has no sources")
    sources: list[Source] = []
    for s in raw_sources[:MAX_SOURCES]:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(s, dict):
            raise BriefError("a source is not an object")
        sd: dict[str, Any] = s  # pyright: ignore[reportUnknownVariableType]
        url = _url(sd.get("url"))
        title = sd.get("title")
        name = (
            _text(title, MAX_TITLE) if isinstance(title, str) and title.strip() else ""
        )
        sources.append(Source(url, name or (urlsplit(url).hostname or "")))
    return Item(
        topic=topic,
        headline=_text(d.get("headline"), MAX_HEADLINE),
        summary=_text(d.get("summary"), MAX_SUMMARY),
        change=change,  # pyright: ignore[reportArgumentType]
        on=on,
        sources=tuple(sources),
    )


def parse(text: str, topics: Sequence[str], today: date) -> tuple[list[Item], int]:
    """The kept items, deduped within the reply, and how many were dropped.
    `topics` are the user's own (never the decoys). Raises BriefError when
    the reply is not a JSON list of items."""
    body = text.strip()
    if body.startswith("```"):  # a fenced block: ```json ... ```
        body = body.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        raw: Any = json.loads(body)
    except ValueError:
        raise BriefError("the reply is not JSON") from None
    if isinstance(raw, dict) and "items" in raw:
        raw = raw["items"]  # pyright: ignore[reportUnknownVariableType]
    if not isinstance(raw, list):
        raise BriefError("the reply is not a list of items")
    by_lower = {t.lower(): t for t in topics}
    kept: dict[str, Item] = {}
    dropped = 0
    for r in raw[:MAX_ITEMS]:  # pyright: ignore[reportUnknownVariableType]
        try:
            it = _item(r, by_lower, today)
        except BriefError:
            dropped += 1
            continue
        if it.dedupe in kept:
            dropped += 1
        else:
            kept[it.dedupe] = it
    return list(kept.values()), dropped + max(0, len(raw) - MAX_ITEMS)  # pyright: ignore[reportUnknownArgumentType]
