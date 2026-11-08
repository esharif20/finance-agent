"""`validate` a cloud reply against its PlaceholderMap, then `fill` it locally.

A reply passes only as words and known placeholders: no figure or digit in any
script, no currency sign, no spelt-out number, no Merchant or user-made
Category name, no hidden characters, nothing instruction-like, and not too
long. Everything is checked on the NFKC form, which is also the text `fill`
uses, so a full-width "｛P1｝" cannot slip past as something else. The grounding
check then runs, unchanged, on the filled text."""

import re
import unicodedata
from datetime import date

from finance_agent.gateway.redact import NUMBER_WORDS, fold, name_pattern
from finance_agent.gateway.types import (
    PLACEHOLDER,
    Category,
    CloudReply,
    Merchant,
    Money,
    Month,
    Note,
    Percent,
    PlaceholderMap,
    Rejected,
    Value,
)

MAX_REPLY = 1500

# "one" alone is a word ("no one", "which one"); with a unit it is a figure.
_NUMBER = re.compile(
    rf"\b(?:{NUMBER_WORDS.replace('one|', '')})\b"
    r"|\bone\s+(?:pounds?|quid|pence|p|percent|per\s?cent|dollars?|euros?)\b",
    re.IGNORECASE,
)

# Text aimed at a model or a browser, or a way out of the page: markup, links,
# role markers, injection verbs. Wording about money never needs any of it.
_INSTRUCTION = re.compile(
    r"""[<>`\\|] | \[\[ | \]\( | \*\* | https?: | :// | \bwww\. | @[a-z]
    | \b[a-z0-9-]+\.(?:com|net|org|io|ai|co|uk|xyz|dev|app)\b
    | \b(?:mailto|data|javascript|file)\s*:
    | \b(?:system|assistant|user|tool|developer|human|model)\s*:
    | \b(?:ignor|disregard|forget|overrid|bypass)\w*\s+(?:\w+\s+){0,3}?
      (?:previous|prior|above|earlier|all|instructions?|rules?|guidelines?)\b
    | \binstructions?\b | \bprompt\b | \bjailbreak | \byou\s+are\s+now\b
    | \bact\s+as\b | \bfrom\s+now\s+on\b | \bpretend\b
    | \b(?:call|invoke|run|use)\s+(?:the\s+|a\s+)?(?:tool|function)\b
    | \bapi\s*key\b | \bpassword\b | \bplaceholders?\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

_HIDDEN = {
    "Cc",
    "Cf",
    "Co",
    "Cs",
    "Cn",
}  # control, format, private, surrogate, unassigned


def validate(reply: str, placeholders: PlaceholderMap) -> CloudReply | Rejected:
    if len(reply) > MAX_REPLY * 2:  # before NFKC, which can grow the text
        return Rejected(reason="too_long")
    text = unicodedata.normalize("NFKC", reply).strip()
    if not text:
        return Rejected(reason="empty")
    if len(text) > MAX_REPLY:
        return Rejected(reason="too_long")
    if any(unicodedata.category(ch) in _HIDDEN and ch not in "\n\t" for ch in text):
        return Rejected(reason="hidden_text")
    if any(m[0] not in placeholders for m in PLACEHOLDER.finditer(text)):
        return Rejected(reason="unknown_placeholder")
    rest = PLACEHOLDER.sub(" ", text)
    if "{" in rest or "}" in rest:  # "{P 1}", "{p1}", "{{P1}}", "{P01}"
        return Rejected(reason="unknown_placeholder")
    if any(ch.isnumeric() or unicodedata.category(ch) == "Sc" for ch in rest):
        return Rejected(reason="figure")
    folded = fold(rest)
    if _NUMBER.search(folded):
        return Rejected(reason="figure")
    for ph in placeholders.placeholders():
        v = placeholders[ph]
        if isinstance(v, Merchant | Category) and (p := name_pattern(v.name)):
            if re.search(p, folded, re.IGNORECASE):
                return Rejected(reason="merchant")
    if _INSTRUCTION.search(folded):
        return Rejected(reason="instruction")
    return CloudReply(text=text)


_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}


def display(value: Value) -> str:
    """How a value reads in a reply; the grounding check parses these forms."""
    if isinstance(value, Money):
        whole, pence = divmod(abs(value.minor), 100)
        amount = f"{whole:,}.{pence:02d}"
        sign = "-" if value.minor < 0 else ""
        symbol = _SYMBOL.get(value.currency)
        return (
            f"{sign}{symbol}{amount}" if symbol else f"{sign}{amount} {value.currency}"
        )
    if isinstance(value, Percent):
        return f"{value.value}%"
    if isinstance(value, Month):
        return date(value.year, value.month, 1).strftime("%B %Y")
    if isinstance(value, date):
        return f"{value.day} {value.strftime('%B %Y')}"
    if isinstance(value, Merchant | Category):
        return value.name
    if isinstance(value, Note):
        raise TypeError("notes never have a placeholder")
    return str(value)


def fill(reply: CloudReply, placeholders: PlaceholderMap) -> str:
    """The reply with real values, for local display only. A placeholder the
    map does not hold raises KeyError: a reply is filled only by its own map."""
    return PLACEHOLDER.sub(lambda m: display(placeholders[m[0]]), reply.text)
