"""The guidance playbook (#254, docs/AI_DESIGN.md, ADR 0007): short cards
written as repo data, shown when an Engine-evaluated condition holds, with
every figure filled from Engine signals or a dated fact.

- **Signals** (`signals`): named figures the Engine computes from the user's
  Spending rows and savings balance. A signal without enough data is `None`.
- **Cards** (`playbook_data/cards/<id>.json`): id, topic, principles, a
  `when` condition and gentle and blunt variants. A title or body may name
  slots: `{<signal>}` for an Engine figure, `{fact:<id>}` for a fact.
- **Conditions**: `{"all" | "any": [...]}` over `{"signal", "op", "value"}`
  clauses. A `None` signal fails its clause, and there is no negation, so a
  card never shows on missing data. Thresholds are typed rule parameters
  (integers, or decimals for a ratio), never prose.
- **Facts** (`playbook_data/facts.json`): statutory figures with a source URL
  (never fetched) and a "checked on" date, always shown with the card. One
  checked before the current tax year began (6 April) shows "may be out of
  date" (#251 decision 6); CI fails one older than 13 months.

Loading validates everything and fails on an unknown signal, slot, fact or
key. Card prose is linted: no digit (any script) outside a slot, no character
outside printable ASCII and a few typographic marks, and no instruction-like
text, so no hand-typed figure or injected instruction can reach the screen
or the Assistant. Everything here is pure and deterministic.
"""

import hashlib
import json
import operator
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any, Literal, cast

from finance_agent.engine.detectors import Row

DATA = Path(__file__).parent / "playbook_data"

type Value = int | Decimal
type Signals = Mapping[str, Value | None]
type Kind = Literal["money", "count", "ratio"]

# name -> how it is shown: money in pence, count, or a ratio to one decimal
SIGNALS: dict[str, Kind] = {
    "essential_monthly_spend": "money",
    "savings_balance": "money",
    "emergency_fund_months": "ratio",
    "subscription_spend_last_month": "money",
    "subscription_rise_streak": "count",
    "subscription_spend_rise": "money",
}
TOPICS = (
    "emergency_fund",
    "subscription_creep",
    "small_daily_spends",
    "saving_for_goal",
    "credit_cards",
    "payday_planning",
    "student_money",
)
TONES = ("gentle", "blunt")
ESSENTIAL_TOPS = {"Bills"}
ESSENTIAL_LEAVES = {
    ("Food & Drink", "Groceries"),
    ("Health", "Pharmacy"),
    ("Transport", "Public transport"),
    ("Transport", "Fuel"),
}
ESSENTIAL_MONTHS = 3
TENTH = Decimal("0.1")
FACT_MAX_MONTHS = 13

OPS: dict[str, Callable[[Any, Any], bool]] = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
}
_ID = re.compile(r"[a-z]+(?:[-_][a-z]+)*")
_SLOT = re.compile(r"\{(fact:)?([a-z]+(?:_[a-z]+)*)\}")
_PROSE_CHARS = re.compile(r"[ -~£‘’“”–—…]*")
_INSTRUCTION = re.compile(
    r"\b(?:ignore|disregard|forget|override|instructions?|prompts?|system"
    r"|assistant|model|llm|tools?|functions?|pretend|jailbreak|act\s+as"
    r"|you\s+are\s+now|from\s+now\s+on|respond|reply|output|print)\b"
    r"|[<>`\\|]|https?:|www\.|@",
    re.IGNORECASE,
)


class PlaybookError(ValueError):
    """Card or fact data that fails validation."""


@dataclass(frozen=True)
class Clause:
    signal: str
    op: str
    value: Value


@dataclass(frozen=True)
class Group:
    mode: Literal["all", "any"]
    parts: tuple["Condition", ...]


type Condition = Clause | Group


@dataclass(frozen=True)
class Variant:
    title: str
    body: str


@dataclass(frozen=True)
class Card:
    id: str
    version: int
    topic: str
    principles: tuple[str, ...]
    when: Condition
    variants: Mapping[str, Variant]


@dataclass(frozen=True)
class Fact:
    id: str
    label: str
    value: int  # pence for money, else a count
    unit: Literal["money", "count"]
    source: str  # https URL, never fetched
    checked_on: date


@dataclass(frozen=True)
class FactShown:
    id: str
    label: str
    value: str
    source: str
    checked_on: date
    may_be_out_of_date: bool


@dataclass(frozen=True)
class Shown:
    card_id: str
    topic: str
    tone: str
    title: str
    body: str
    principles: tuple[str, ...]
    figures: tuple[tuple[str, str], ...]  # (signal, as shown), by name
    facts: tuple[FactShown, ...]  # by id


@dataclass(frozen=True)
class Playbook:
    cards: tuple[Card, ...]  # by id
    facts: Mapping[str, Fact]
    version: str  # sha256 prefix of the data files


# --- signals ----------------------------------------------------------------


def _month(d: date) -> int:
    return d.year * 12 + d.month - 1


def _essential(category: tuple[str, str]) -> bool:
    return category[0] in ESSENTIAL_TOPS or category in ESSENTIAL_LEAVES


def signals(
    rows: Iterable[Row], savings_balance: int | None, today: date
) -> dict[str, Value | None]:
    """Every signal from Spending and Refund rows (signed GBP pence) and the
    balance of the user's savings Accounts (pence; `None` when unknown).

    Months are calendar months after the first row's (which may be partial)
    and before today's (still running). Essentials need the last three; the
    subscription streak needs two."""
    rows = list(rows)
    out: dict[str, Value | None] = dict.fromkeys(SIGNALS)
    out["savings_balance"] = savings_balance
    if not rows:
        return out
    first, last = min(_month(r.posted_on) for r in rows) + 1, _month(today) - 1
    months = range(first, last + 1)
    essentials = dict.fromkeys(months, 0)
    subs = dict.fromkeys(months, 0)
    for r in rows:
        m = _month(r.posted_on)
        if m in essentials:
            if _essential(r.category):
                essentials[m] -= r.amount
            if r.category[0] == "Subscriptions":
                subs[m] -= r.amount
    if len(months) >= ESSENTIAL_MONTHS:
        spend = sum(essentials[m] for m in months[-ESSENTIAL_MONTHS:])
        out["essential_monthly_spend"] = spend // ESSENTIAL_MONTHS
        if savings_balance is not None and spend > 0:
            exact = Decimal(savings_balance * ESSENTIAL_MONTHS) / Decimal(spend)
            # floored, so the figure shown is the figure the condition read
            out["emergency_fund_months"] = exact.quantize(TENTH, ROUND_FLOOR)
    if len(months) >= 2:
        streak = 0
        while (
            streak + 1 < len(months) and subs[last - streak] > subs[last - streak - 1]
        ):
            streak += 1
        out["subscription_rise_streak"] = streak
        out["subscription_spend_last_month"] = subs[last]
        out["subscription_spend_rise"] = subs[last] - subs[last - streak]
    return out


# --- conditions -------------------------------------------------------------


def _keys(raw: Any, keys: set[str], what: str) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != keys:  # pyright: ignore[reportUnknownArgumentType]
        raise PlaybookError(f"{what} must have exactly the keys {sorted(keys)}")
    return raw  # pyright: ignore[reportUnknownVariableType]


def parse_condition(raw: Any) -> Condition:
    if isinstance(raw, dict) and len(cast(dict[str, Any], raw)) == 1:
        ((mode, parts),) = cast(dict[str, Any], raw).items()
        if mode in ("all", "any"):
            if not isinstance(parts, list) or not parts:
                raise PlaybookError(f"{mode} needs a non-empty list")
            items = cast(list[Any], parts)
            return Group(mode, tuple(parse_condition(p) for p in items))
    c = _keys(raw, {"signal", "op", "value"}, "a clause")
    signal, op, value = c["signal"], c["op"], c["value"]
    if signal not in SIGNALS:
        raise PlaybookError(f"unknown signal {signal!r}")
    if op not in OPS:
        raise PlaybookError(f"unknown op {op!r}")
    whole = type(value) is int
    if not (whole or (SIGNALS[signal] == "ratio" and type(value) is Decimal)):
        raise PlaybookError(f"{signal} needs a whole-number threshold")
    return Clause(signal, op, value)


def holds(when: Condition, sigs: Signals) -> bool:
    """Whether a condition holds; a missing or `None` signal fails its clause."""
    if isinstance(when, Group):
        test = all if when.mode == "all" else any
        return test(holds(p, sigs) for p in when.parts)
    v = sigs.get(when.signal)
    return v is not None and OPS[when.op](v, when.value)


# --- cards and facts --------------------------------------------------------


def lint_prose(text: Any, where: str) -> str:
    """Card text: no digit outside a slot, no unexpected character and no
    instruction-like text."""
    if not isinstance(text, str) or not text.strip():
        raise PlaybookError(f"{where}: must be non-empty text")
    if not _PROSE_CHARS.fullmatch(text):
        raise PlaybookError(f"{where}: has a character outside the allowed set")
    bare = _SLOT.sub("", text)
    if any(ch.isnumeric() for ch in bare):
        raise PlaybookError(f"{where}: has a figure outside a slot")
    if "{" in bare or "}" in bare:
        raise PlaybookError(f"{where}: has a malformed slot")
    if _INSTRUCTION.search(bare):
        raise PlaybookError(f"{where}: has instruction-like text")
    return text


def _slots(text: str, facts: Mapping[str, Fact], where: str) -> None:
    for fact, name in _SLOT.findall(text):
        if (name not in facts) if fact else (name not in SIGNALS):
            raise PlaybookError(
                f"{where}: unknown {'fact' if fact else 'slot'} {name!r}"
            )


def parse_card(raw: Any, facts: Mapping[str, Fact]) -> Card:
    c = _keys(
        raw, {"id", "version", "topic", "principles", "when", "variants"}, "a card"
    )
    cid = c["id"]
    if not isinstance(cid, str) or not _ID.fullmatch(cid):
        raise PlaybookError(f"bad card id {cid!r}")
    if type(c["version"]) is not int or c["version"] < 1:
        raise PlaybookError(f"{cid}: version must be a positive integer")
    if c["topic"] not in TOPICS:
        raise PlaybookError(f"{cid}: unknown topic {c['topic']!r}")
    principles = c["principles"]
    if not isinstance(principles, list) or not principles:
        raise PlaybookError(f"{cid}: principles must be a non-empty list")
    for i, p in enumerate(principles):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if _SLOT.search(lint_prose(p, f"{cid} principle {i}")):
            raise PlaybookError(f"{cid}: principles take no slots")
    variants = _keys(c["variants"], set(TONES), f"{cid} variants")
    parsed: dict[str, Variant] = {}
    for tone in TONES:
        v = _keys(variants[tone], {"title", "body"}, f"{cid} {tone}")
        for part in ("title", "body"):
            where = f"{cid} {tone} {part}"
            _slots(lint_prose(v[part], where), facts, where)
        parsed[tone] = Variant(v["title"], v["body"])
    return Card(
        cid,
        c["version"],
        c["topic"],
        tuple(principles),  # pyright: ignore[reportUnknownArgumentType]
        parse_condition(c["when"]),
        parsed,
    )


def parse_facts(raw: Any) -> dict[str, Fact]:
    items = _keys(raw, {"facts"}, "facts.json")["facts"]
    if not isinstance(items, list):
        raise PlaybookError("facts must be a list")
    facts: dict[str, Fact] = {}
    for item in items:  # pyright: ignore[reportUnknownVariableType]
        f = _keys(
            item, {"id", "label", "value", "unit", "source", "checked_on"}, "a fact"
        )
        fid = f["id"]
        if (
            not isinstance(fid, str)
            or "-" in fid
            or not _ID.fullmatch(fid)
            or fid in facts
        ):
            raise PlaybookError(f"bad or repeated fact id {fid!r}")
        lint_prose(f["label"], f"fact {fid} label")
        if type(f["value"]) is not int or f["unit"] not in ("money", "count"):
            raise PlaybookError(
                f"fact {fid}: value must be an integer, unit money or count"
            )
        if not isinstance(f["source"], str) or not re.fullmatch(
            r"https://[^\s]+", f["source"]
        ):
            raise PlaybookError(f"fact {fid}: source must be an https URL")
        try:
            checked = date.fromisoformat(f["checked_on"])
        except (TypeError, ValueError):
            raise PlaybookError(f"fact {fid}: checked_on must be YYYY-MM-DD") from None
        facts[fid] = Fact(fid, f["label"], f["value"], f["unit"], f["source"], checked)
    return facts


def stale_facts(facts: Mapping[str, Fact], today: date) -> list[str]:
    """Facts checked over `FACT_MAX_MONTHS` ago or in the future (CI fails)."""
    return sorted(
        f.id
        for f in facts.values()
        if f.checked_on > today
        or (_month(today), today.day)
        > (_month(f.checked_on) + FACT_MAX_MONTHS, f.checked_on.day)
    )


def load(data: Path = DATA) -> Playbook:
    """Every card and fact, validated. A card file is named `<id>.json`."""
    facts_path = data / "facts.json"
    paths = sorted((data / "cards").glob("*.json"))
    facts = parse_facts(_json(facts_path))
    cards: list[Card] = []
    for path in paths:
        card = parse_card(_json(path), facts)
        if card.id != path.stem:
            raise PlaybookError(f"{path.name}: id {card.id!r} must match the file name")
        cards.append(card)
    digest = hashlib.sha256()
    for path in [facts_path, *paths]:
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return Playbook(
        tuple(sorted(cards, key=lambda c: c.id)), facts, digest.hexdigest()[:12]
    )


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except json.JSONDecodeError as e:
        raise PlaybookError(f"{path.name}: {e}") from None


# --- rendering --------------------------------------------------------------


def money(pence: int) -> str:
    """£1,234.56; whole pounds without pence, as £20,000."""
    sign, pence = ("-" if pence < 0 else ""), abs(pence)
    pounds, p = divmod(pence, 100)
    return f"{sign}£{pounds:,}" + (f".{p:02d}" if p else "")


def _shown(kind: Kind, v: Value) -> str:
    if isinstance(v, int):  # money and counts are whole; a ratio is a Decimal
        return money(v) if kind == "money" else f"{v:,}"
    return str(v)


def tax_year_start(today: date) -> date:
    start = date(today.year, 4, 6)
    return start if today >= start else date(today.year - 1, 4, 6)


def render(
    card: Card, sigs: Signals, facts: Mapping[str, Fact], tone: str, today: date
) -> Shown | None:
    """The card in `tone` with its slots filled, or `None` when its condition
    does not hold or a slot it names has no figure."""
    if not holds(card.when, sigs):
        return None
    v = card.variants[tone]
    figures: dict[str, str] = {}
    shown_facts: dict[str, FactShown] = {}
    for fact, name in _SLOT.findall(v.title + v.body):
        if fact:
            f = facts[name]
            value = money(f.value) if f.unit == "money" else f"{f.value:,}"
            shown_facts[name] = FactShown(
                f.id,
                f.label,
                value,
                f.source,
                f.checked_on,
                f.checked_on < tax_year_start(today),
            )
        else:
            figure = sigs.get(name)
            if figure is None:
                return None
            figures[name] = _shown(SIGNALS[name], figure)

    def fill(text: str) -> str:
        return _SLOT.sub(
            lambda m: shown_facts[m[2]].value if m[1] else figures[m[2]], text
        )

    return Shown(
        card.id,
        card.topic,
        tone,
        fill(v.title),
        fill(v.body),
        card.principles,
        tuple(sorted(figures.items())),
        tuple(shown_facts[k] for k in sorted(shown_facts)),
    )
