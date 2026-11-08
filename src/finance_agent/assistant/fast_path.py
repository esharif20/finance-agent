"""The fast path: a simple spending question ("how much did I spend on fuel
last month?") parsed by rules into one typed tool call, answered from a
template with the Engine's figures. No model call (ADR 0007: the Engine
computes, the template only words it; the reply still passes grounding).

`parse` is strict: the whole message must be one known shape naming at most
one taxonomy Category and one month, else None and the model answers.
`guess` is loose: the Category and month a message mentions, for the
retrieve-then-generate context. shortcut: taxonomy names and plurals only, no
synonyms (petrol, car); add an alias table if the routing set shows misses."""

import re
from datetime import date

from pydantic import BaseModel

from finance_agent.assistant.chat_model import ToolCall
from finance_agent.assistant.grounding import Money, Month, format_figure
from finance_agent.assistant.tools.read import CategoryDetailOut, SpendingSummaryOut
from finance_agent.assistant.tools.read import Money as ToolMoney
from finance_agent.ledger.taxonomy import TAXONOMY, UNCATEGORISED

_MONTHS: tuple[str, ...] = tuple(
    "jan feb mar apr may jun jul aug sep oct nov dec".split()
)
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_WHEN = (
    rf"(?:last month|this month|(?:in )?(?P<mon>{_MON})(?: (?P<year>\d{{4}}))?"
    r"|(?:in )?(?P<ym>\d{4}-(?:0[1-9]|1[0-2])))"
)
_SIMPLE = re.compile(
    r"^(?:how much did i spend|what did i spend)"
    r"(?: (?:on|in) (?P<what>[a-z&' ]+?))??"
    rf"(?: (?P<when>{_WHEN}))?\s*\??$",
    re.IGNORECASE,
)


def _names() -> dict[str, str]:
    """Spoken name -> the Category as category_detail takes it."""
    out: dict[str, str] = {}
    for top, subs in TAXONOMY.items():
        if top == UNCATEGORISED:
            continue
        for sub in subs:
            out[sub.casefold()] = f"{top} > {sub}"
        out[top.casefold()] = top
    for k, v in list(out.items()):
        out[k.replace("&", "and")] = v
        out.setdefault(f"{k}s", v)
    return out


NAMES = _names()


def _month(m: re.Match[str], today: date) -> str | None:
    when = (m["when"] or "").casefold()
    if not when:
        return None
    if m["ym"]:
        return m["ym"]
    if m["mon"]:
        mon = _MONTHS.index(m["mon"][:3].casefold()) + 1
        year = int(m["year"]) if m["year"] else today.year - (mon > today.month)
        return f"{year}-{mon:02d}"
    n = today.year * 12 + today.month - 1 - when.startswith("last")
    return f"{n // 12}-{n % 12 + 1:02d}"


def parse(message: str, today: date) -> ToolCall | None:
    """The one tool call a simple spending question needs, or None."""
    m = _SIMPLE.match(" ".join(message.split()))
    if m is None:
        return None
    month = _month(m, today)
    args: dict[str, str] = {"month": month} if month else {}
    if m["what"] is None:
        return ToolCall(name="spending_summary", arguments=dict(args))
    category = NAMES.get(m["what"].casefold().removeprefix("my "))
    if category is None:
        return None
    return ToolCall(name="category_detail", arguments={"category": category, **args})


_ANY_MONTH = re.compile(rf"\b(?P<when>{_WHEN})\b", re.IGNORECASE)
_WORDS = sorted(NAMES, key=len, reverse=True)


def guess(message: str, today: date) -> ToolCall | None:
    """The Category (and month) a message names, as a category_detail call."""
    text = " ".join(message.casefold().split())
    name = next((n for n in _WORDS if re.search(rf"\b{re.escape(n)}\b", text)), None)
    if name is None:
        return None
    args = {"category": NAMES[name]}
    if (m := _ANY_MONTH.search(text)) is not None and (month := _month(m, today)):
        args["month"] = month
    return ToolCall(name="category_detail", arguments=dict(args))


def render(out: BaseModel) -> str | None:
    """The template reply for the tool's result."""
    if isinstance(out, CategoryDetailOut):
        name = out.category.rpartition(" > ")[2]
        spent, month = _pounds(out.total), _words(out.month)
        return f"You spent {spent} on {name} in {month}."
    if isinstance(out, SpendingSummaryOut):
        gap = " Some of that month's data is missing." if out.incomplete else ""
        return f"You spent {_pounds(out.spending)} in {_words(out.month)}.{gap}"
    return None


def _words(month: str) -> str:
    return format_figure(Month(year=int(month[:4]), month=int(month[5:])))


def _pounds(m: ToolMoney) -> str:
    return format_figure(Money(minor=m.minor, currency=m.currency))
