"""The grounding check (#137, ADR 0007, docs/AI_DESIGN.md): every money figure,
percentage, date and count in an Assistant reply must equal a value from this
turn's tool results. Matching is exact after normalising the format; a rounded
figure passes only when a tool supplied the rounded value.

Fail-closed: any digit the parser cannot read as a supported figure, and any
figure spelled out in words, is reported as offending. On failure the graph
(#139) regenerates once, then shows `render_fallback`.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)


class Money(BaseModel):
    model_config = _STRICT
    minor: int  # signed: an outflow a tool reports as negative must be written so
    currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$")


class Percent(BaseModel):
    model_config = _STRICT
    value: Decimal  # 12.5 means 12.5%


class Month(BaseModel):
    """A whole calendar month, as in "March 2026"."""

    model_config = _STRICT
    year: int
    month: int = Field(ge=1, le=12)


# A plain `int` is a count ("3 subscriptions").
type Figure = Money | Percent | Month | date | int


class ToolValue(BaseModel):
    """One figure from a tool result, with the evidence ID it came from."""

    model_config = _STRICT
    evidence_id: str = Field(min_length=1)
    label: str = ""
    value: Figure


class Span(BaseModel):
    model_config = _STRICT
    start: int  # indices into the original reply
    end: int
    text: str


class GroundingResult(BaseModel):
    model_config = _STRICT
    grounded: bool
    offending: tuple[Span, ...]
    evidence_ids: tuple[str, ...]


# A claim read from the reply: one figure, a day and month without a year, or
# None for something that can never be grounded (a spelled-out amount, stray digits).
type _Claim = Figure | tuple[Literal["day_month"], int, int] | None

_MONTHS = (
    "january february march april may june july august september october "
    "november december"
).split()
_MONTH_RE = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_WEEKDAYS: tuple[str, ...] = tuple(
    "monday tuesday wednesday thursday friday saturday sunday".split()
)
_NUM = r"(?P<num>(?>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?))"
# a sign must touch the figure, so a Markdown bullet ("- £40") is not a minus
_SIGN = r"(?:(?<![\w%.£$€])(?P<sign>[-+]))?"
_END = r"(?![\w])"
_CURRENCY = {"£": "GBP", "$": "USD", "€": "EUR"}
_CODE = r"(?:gbp|usd|eur)"
_UNITS = dict(
    gbp="GBP", pound="GBP", quid="GBP", usd="USD", dollar="USD", eur="EUR", euro="EUR"
)
_MULT = {"k": 3, "thousand": 3, "m": 6, "million": 6, "bn": 9, "billion": 9}
_WORD_NUMBERS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
    "|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty"
    "|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion"
)

# Characters that render as nothing, and Markdown emphasis, both of which could
# split a figure ("£1**2**.35", "£1​2") so the parser misses it.
_INVISIBLE = set("­᠎​‌‍⁠﻿*_`~\\") | {
    chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))
}
_MINUS = set("−‐‑‒﹣")


def _normalise(reply: str) -> tuple[str, list[int]]:
    """The reply as the parser reads it, and each character's index in the original."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(reply):
        if ch in _INVISIBLE:
            continue
        if ch in _MINUS:
            text = "-"
        elif unicodedata.decimal(ch, None) is not None:
            text = str(unicodedata.decimal(ch))  # any script's digits, e.g. full-width
        else:
            text = unicodedata.normalize("NFKC", ch)
        out.append(text)
        index.extend([i] * len(text))
    return "".join(out), index


def _decimal(num: str) -> Decimal:
    return Decimal(num.replace(",", ""))


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_number(name: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(name[:3].lower()))


def _money(m: re.Match[str], currency: str, minor_unit: bool = False) -> _Claim:
    amount = _decimal(m["num"])
    groups = m.groupdict()
    mult = (groups.get("mult") or groups.get("mword") or "").lower()
    if mult:
        amount = amount.scaleb(_MULT[mult])
    minor = amount if minor_unit else amount * 100
    if minor != minor.to_integral_value():
        return None  # finer than a penny: no tool value can match
    signs = [s for s in (groups.get("sign"), groups.get("sign2")) if s]
    if len(signs) > 1:
        return None
    value = int(minor)
    return Money(minor=-value if signs == ["-"] else value, currency=currency)


def _symbol_money(m: re.Match[str]) -> _Claim:
    return _money(m, _CURRENCY.get(m["cur"].strip(), m["cur"].strip().upper()))


def _relative_day(m: re.Match[str], today: date) -> _Claim:
    word = m["rel"].lower()
    if word in ("today", "yesterday", "tomorrow"):
        return today + timedelta(
            days={"today": 0, "yesterday": -1, "tomorrow": 1}[word]
        )
    target = _WEEKDAYS.index(m["day"].lower())
    if m["dir"].lower() == "last":
        return today - timedelta(days=(today.weekday() - target) % 7 or 7)
    return today + timedelta(days=(target - today.weekday()) % 7 or 7)


def _day_month(m: re.Match[str]) -> _Claim:
    month, day = _month_number(m["mon"]), int(m["day"])
    if m["year"]:
        return _make_date(int(m["year"]), month, day)
    if _make_date(2024, month, day) is None:  # 2024 is a leap year, so 29 Feb is valid
        return None
    return ("day_month", day, month)


type _Rule = tuple[re.Pattern[str], Callable[[re.Match[str], date], _Claim]]


def _rule(pattern: str, read: Callable[[re.Match[str], date], _Claim]) -> _Rule:
    return re.compile(pattern, re.IGNORECASE), read


# Most specific first; each rule sees only text no earlier rule consumed.
_RULES: tuple[_Rule, ...] = (
    _rule(
        rf"\b(?P<rel>today|yesterday|tomorrow|(?P<dir>last|next)\s+"
        rf"(?P<day>{'|'.join(_WEEKDAYS)}))\b",
        _relative_day,
    ),
    _rule(
        r"(?<![\w./-])(?P<d>\d{1,2})/(?P<m>\d{1,2})/(?P<y>\d{4})(?![\w/])",
        lambda m, _: _make_date(int(m["y"]), int(m["m"]), int(m["d"])),
    ),
    _rule(
        r"(?<![\w./-])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\w-])",
        lambda m, _: _make_date(int(m["y"]), int(m["m"]), int(m["d"])),
    ),
    _rule(
        rf"(?<![\w.,])(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\b"
        r"(?:,?\s+(?P<year>\d{4})\b)?",
        lambda m, _: _day_month(m),
    ),
    _rule(
        rf"\b{_MONTH_RE}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(?P<year>\d{{4}})\b)?",
        lambda m, _: _day_month(m),
    ),
    _rule(
        rf"\b{_MONTH_RE}\s+(?P<year>\d{{4}})\b",
        lambda m, _: Month(year=int(m["year"]), month=_month_number(m["mon"])),
    ),
    # £12.35, -£40, £1.2k, £3 million, GBP 40, $12, €12
    _rule(
        rf"{_SIGN}(?P<cur>[£$€]|\b{_CODE})\s?(?P<sign2>[-+])?{_NUM}"
        rf"(?:(?P<mult>k|m|bn)|\s(?P<mword>thousand|million|billion))?{_END}",
        lambda m, _: _symbol_money(m),
    ),
    # 40 GBP, 40 pounds, 40 quid, 40 dollars
    _rule(
        rf"{_SIGN}(?<![\w.,]){_NUM}\s?(?P<unit>{_CODE}|pounds?|quid|dollars?|euros?){_END}",
        lambda m, _: _money(m, _UNITS[m["unit"].lower().rstrip("s")]),
    ),
    # 40p, 40 pence
    _rule(
        rf"{_SIGN}(?<![\w.,]){_NUM}(?:p|\s?pence){_END}",
        lambda m, _: _money(m, "GBP", minor_unit=True),
    ),
    # a bare 1.2k is pounds
    _rule(rf"{_SIGN}(?<![\w.,]){_NUM}(?P<mult>k){_END}", lambda m, _: _money(m, "GBP")),
    _rule(
        rf"{_SIGN}(?<![\w.,]){_NUM}\s?(?:%|percent\b|per\s?cent\b)",
        lambda m, _: Percent(
            value=-_decimal(m["num"]) if m["sign"] == "-" else _decimal(m["num"])
        ),
    ),
    # a whole number standing alone is a count
    _rule(r"(?<![\w.,/:-])(?P<n>\d+)(?![\w/:-]|[.,]\d)", lambda m, _: int(m["n"])),
    # figures in words are never grounded: "twelve pounds", "£twelve", "ten percent".
    # Only the last number word is matched ("five pounds" in "thirty-five pounds"):
    # matching the whole run is quadratic on a long run of number words.
    _rule(
        rf"\b(?:{_WORD_NUMBERS})\s+"
        r"(?:pounds?|quid|pence|p|percent|per\s?cent|grand|dollars?|euros?)\b"
        rf"|[£$€]\s?(?:{_WORD_NUMBERS})\b",
        lambda m, _: None,
    ),
    _rule(r"\d+", lambda m, _: None),  # any digit left over
)


def _matches(claim: _Claim, value: Figure) -> bool:
    if claim is None:
        return False
    if isinstance(claim, tuple):
        return isinstance(value, date) and (value.day, value.month) == claim[1:]
    return type(value) is type(claim) and value == claim


def check_grounding(
    reply: str, values: Iterable[ToolValue], *, today: date
) -> GroundingResult:
    """Ground every figure in `reply` against this turn's tool `values`.

    `today` resolves relative dates ("yesterday", "last Tuesday")."""
    values = tuple(values)
    text, index = _normalise(reply)
    offending: list[Span] = []
    evidence: set[str] = set()
    for pattern, read in _RULES:
        for m in pattern.finditer(text):
            try:
                claim = read(m, today)
            except (InvalidOperation, ValueError, OverflowError):
                claim = None
            used = {v.evidence_id for v in values if _matches(claim, v.value)}
            start, end = index[m.start()], index[m.end() - 1] + 1
            if used:
                evidence |= used
            else:
                offending.append(Span(start=start, end=end, text=reply[start:end]))
        # consumed text cannot be read again; \x00 keeps the indices aligned
        text = pattern.sub(lambda m: "\x00" * len(m[0]), text)
    offending.sort(key=lambda s: s.start)
    return GroundingResult(
        grounded=not offending,
        offending=tuple(offending),
        evidence_ids=tuple(sorted(evidence)),
    )


def format_figure(value: Figure) -> str:
    """A figure in a format `check_grounding` reads back as the same value."""
    if isinstance(value, Money):
        pounds, pence = divmod(abs(value.minor), 100)
        symbol = {v: k for k, v in _CURRENCY.items()}.get(value.currency)
        number = f"{pounds:,}.{pence:02d}"
        body = f"{symbol}{number}" if symbol else f"{value.currency} {number}"
        return f"-{body}" if value.minor < 0 else body
    if isinstance(value, Percent):
        return f"{value.value:f}%"
    if isinstance(value, Month):
        return f"{_MONTHS[value.month - 1].title()} {value.year}"
    if isinstance(value, date):
        return f"{value.day} {_MONTHS[value.month - 1].title()} {value.year}"
    return str(value)


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_fallback(values: Sequence[ToolValue]) -> str:
    """The deterministic reply shown when a reply fails grounding twice: this
    turn's tool values as a plain Markdown table, with no model-written text."""
    if not values:
        return "I couldn't check that answer, and no figures were found for it."
    rows = [
        f"| {_cell(v.label)} | {format_figure(v.value)} | {_cell(v.evidence_id)} |"
        for v in values
    ]
    return "\n".join(
        [
            "I couldn't check my answer, so here are the figures it was based on.",
            "",
            "| Item | Value | Evidence |",
            "| --- | --- | --- |",
            *rows,
        ]
    )
