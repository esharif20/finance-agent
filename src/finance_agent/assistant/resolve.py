"""Code resolves every reference in a question (#317); the model resolves none.

Each resolver reads the user's own data and returns what it chose plus a
label that shows the choice ("Groceries + Eating out", "1 January 2026 -
20 January 2026"), so the answer or the evidence panel can show it and the
user can correct it. Labels hold figures only as `values` (ToolValues under
the evidence ID `resolved`), so the grounding check accepts them and nothing
else. When a reference matches more than one thing and the choice would change
the figure, `Resolution.question` asks once instead of guessing.

The table it implements is in #317's resolver comment: periods, Merchants,
Categories through a synonym map, Accounts, "big" purchases, Subscriptions and
Bills, income, people and rent through Memories, "usual", "this year" and
currency. Nothing here logs text or figures."""

import difflib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from finance_agent.assistant import semantic
from finance_agent.assistant.grounding import Money, Month, Percent, ToolValue
from finance_agent.assistant.grounding import format_figure as fig
from finance_agent.categorise.display_names import display_name
from finance_agent.ledger import CURRENCY, Ledger
from finance_agent.ledger.proposals import Memory
from finance_agent.ledger.taxonomy import TAXONOMY, UNCATEGORISED

EVIDENCE = "resolved"
BIG_SHARE = 10  # "big" is over the user's own top 10% single payment
FUZZY = 0.85
_MONTHS: tuple[str, ...] = tuple(
    "jan feb mar apr may jun jul aug sep oct nov dec".split()
)
_MON = r"(?P<mon>jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "twelve": 12}

type Compare = Literal["previous", "median3", "year"]


@dataclass(frozen=True)
class Period:
    since: date
    until: date
    label: str

    def months(self) -> tuple[str, str]:
        return f"{self.since:%Y-%m}", f"{self.until:%Y-%m}"

    def whole(self, today: date) -> bool:
        """Whole calendar months, or running to today: what the Engine's
        monthly totals answer exactly."""
        end = _month_end(self.until)
        return self.since.day == 1 and self.until in (end, today)


@dataclass
class Resolution:
    period: Period | None = None
    categories: tuple[str, ...] = ()  # "Top" or "Top > Sub"
    merchant_ids: tuple[int, ...] = ()
    account_id: int | None = None
    threshold_minor: int | None = None  # "big": over this single payment
    income: bool = False
    compare: Compare | None = None
    currency: str | None = None  # "in euros": the Engine converts
    merchant_label: str = ""
    labels: list[str] = field(default_factory=list[str])
    values: list[ToolValue] = field(default_factory=list[ToolValue])
    question: str | None = None  # asked once when a choice changes the figure
    note: str = ""  # Merchants counted from elsewhere, said with the answer
    read: frozenset[str] = frozenset()  # words the semantic index read

    def value(self, label: str, v: Money | Month | Percent | date | int) -> None:
        self.values.append(ToolValue(evidence_id=EVIDENCE, label=label, value=v))


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w&'£€$.-]+", " ", text.casefold()).split())


def _month_end(d: date) -> date:
    n = d.year * 12 + d.month  # the next month
    return date(n // 12, n % 12 + 1, 1) - timedelta(days=1)


def _shift(d: date, months: int) -> date:
    n = d.year * 12 + d.month - 1 + months
    return date(n // 12, n % 12 + 1, 1)


def month_period(since: date, until: date | None = None) -> Period:
    """Whole months from `since`'s to `until`'s (default the same month)."""
    first = date(since.year, since.month, 1)
    last = _month_end(until or since)
    return Period(first, last, period_label(first, last))


def period_label(since: date, until: date) -> str:
    if since.day == 1 and until == _month_end(since):
        return fig(Month(year=since.year, month=since.month))
    return f"{fig(since)} - {fig(until)}"


def period_values(r: Resolution, p: Period) -> None:
    r.value("from", p.since)
    r.value("to", p.until)
    if p.since.day == 1:
        r.value("month", Month(year=p.since.year, month=p.since.month))
        r.value("month", Month(year=p.until.year, month=p.until.month))


def period(text: str, today: date) -> Period | None:
    """'last month', 'since March', 'this week', 'this year', 'the tax year',
    'last 3 months', 'March 2026', '2026-03', as exact dates from `today`."""
    t = _norm(text)
    this = date(today.year, today.month, 1)
    if re.search(r"\bthis month\b", t):
        return Period(this, today, period_label(this, today))
    if re.search(r"\blast month\b|\bthe month before\b", t):
        return month_period(_shift(this, -1))
    if re.search(r"\bthis week\b", t):
        start = today - timedelta(days=today.weekday())
        return Period(start, today, period_label(start, today))
    if re.search(r"\blast week\b", t):
        end = today - timedelta(days=today.weekday() + 1)
        return Period(
            end - timedelta(days=6), end, period_label(end - timedelta(6), end)
        )
    if re.search(r"\btax year\b", t):
        start = date(today.year - (today < date(today.year, 4, 6)), 4, 6)
        return Period(start, today, period_label(start, today))
    if re.search(r"\bthis year\b", t):
        start = date(today.year, 1, 1)
        return Period(start, today, period_label(start, today))
    if re.search(r"\blast year\b", t):
        y = today.year - 1
        return Period(
            date(y, 1, 1),
            date(y, 12, 31),
            f"{fig(date(y, 1, 1))} - {fig(date(y, 12, 31))}",
        )
    if m := re.search(
        r"\b(?:last|past) (\d{1,2}|one|two|three|four|six|twelve) months\b", t
    ):
        n = int(m[1]) if m[1].isdigit() else _WORDS[m[1]]
        if 1 <= n <= 24:
            return month_period(_shift(this, -n), _shift(this, -1))
    if m := re.search(rf"\bsince {_MON}(?: (?P<year>\d{{4}}))?\b", t):
        start = _named(m, today)
        return Period(start, today, period_label(start, today))
    if m := re.search(r"\b(?P<y>\d{4})-(?P<m>0[1-9]|1[0-2])\b", t):
        return month_period(date(int(m["y"]), int(m["m"]), 1))
    if m := re.search(rf"\b(?:in |for |during )?{_MON}(?: (?P<year>\d{{4}}))?\b", t):
        if m["mon"] != "may" or m["year"] or re.search(r"\b(?:in|for|during) may\b", t):
            start = _named(m, today)
            return (
                month_period(start)
                if start != this
                else Period(this, today, period_label(this, today))
            )
    return None


_MENTION = re.compile(
    rf"\b(?:(?:this|last|past) (?:month|week|year|\d{{1,2}} months|"
    rf"one|two|three|four|six|twelve)|month before|tax year|\d{{4}}-\d{{2}}|"
    rf"{_MON.replace('(?P<mon>', '(?:')})"
)


def one_period(text: str, today: date) -> Period | None:
    """The period when the message names exactly one ("this year"); None
    for a list or a correction ("October, November and December", "last
    month? I mean March"), which the model reads from the words."""
    return period(text, today) if len(_MENTION.findall(_norm(text))) == 1 else None


def _named(m: re.Match[str], today: date) -> date:
    mon = _MONTHS.index(m["mon"]) + 1
    year = int(m["year"]) if m["year"] else today.year - (mon > today.month)
    return date(year, mon, 1)


# --- Categories --------------------------------------------------------------

# spoken words -> Category leaves; never left to the model (#317)
SYNONYMS: dict[str, tuple[str, ...]] = {
    "food": ("Food & Drink > Groceries", "Food & Drink > Eating out"),
    "eating out": ("Food & Drink > Eating out",),
    "restaurants": ("Food & Drink > Eating out",),
    "takeaways": ("Food & Drink > Takeaway",),
    "takeaway": ("Food & Drink > Takeaway",),
    "petrol": ("Transport > Fuel",),
    "diesel": ("Transport > Fuel",),
    "taxis": ("Transport > Taxi",),
    "trains": ("Transport > Public transport",),
    "the tube": ("Transport > Public transport",),
    "gym": ("Health > Fitness",),
    "bills": ("Bills",),
}


def _taxonomy_names() -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for top, subs in TAXONOMY.items():
        if top == UNCATEGORISED:
            continue
        for sub in subs:
            out.setdefault(sub.casefold(), (f"{top} > {sub}",))
        out[top.casefold()] = (top,)
    for k, v in list(out.items()):
        out.setdefault(k.replace("&", "and"), v)
        out.setdefault(f"{k}s", v)
    return out | SYNONYMS


NAMES = _taxonomy_names()
_BY_LENGTH = sorted(NAMES, key=len, reverse=True)


def category_label(cats: Sequence[str]) -> str:
    return " + ".join(c.rpartition(" > ")[2] for c in cats)


def categories(text: str) -> tuple[tuple[str, ...], str]:
    """The Category leaves a message names, longest phrase first, and the label."""
    t = f" {_norm(text)} "
    found: list[str] = []
    for name in _BY_LENGTH:
        pat = rf"(?<=\s){re.escape(name)}(?=[\s?.!,])"
        if re.search(pat, t):
            found += [c for c in NAMES[name] if c not in found]
            t = re.sub(pat, " # ", t)
    return tuple(found), category_label(found)


# --- Merchants ---------------------------------------------------------------

_STOP = frozenset(
    "i me my the a an on at in for of and or to how much what did do spend spent"
    " cost about by vs with from was is this last month year week since".split()
)


def merchants(
    text: str, known: Sequence[tuple[int, str]]
) -> tuple[tuple[int, ...], str, str | None]:
    """Merchant ids a message names by name or fuzzy match: "Uber" covers
    "Uber" and "Uber Eats" when both exist. A fuzzy word that fits two
    Merchants is ambiguous: (ids, label, the question to ask)."""
    t = _norm(text)
    words = [w for w in t.split() if w not in _STOP and len(w) > 2]
    names = [(mid, display_name(n)) for mid, n in known]
    hits: dict[int, str] = {}
    for mid, name in names:
        n = name.casefold()
        if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", t) or any(
            n.split()[0] == w for w in words
        ):
            hits[mid] = name
    if hits:
        # a name inside another ("Tesco" in "Tesco Fuel"): both, as asked
        return tuple(sorted(hits)), " + ".join(sorted(set(hits.values()))), None
    firsts = {name.casefold().split()[0]: name for _, name in names if name.strip()}
    for w in words:
        if w in NAMES:
            continue
        close = difflib.get_close_matches(w, list(firsts), n=3, cutoff=FUZZY)
        picked = {firsts[c] for c in close}
        if len(picked) > 1:
            options = " or ".join(sorted(picked))
            return (), "", f"Did you mean {options}?"
        if picked:
            name = picked.pop()
            ids = tuple(sorted(mid for mid, n in names if n == name))
            return ids, name, None
    return (), "", None


# --- the rest of the table ---------------------------------------------------


def account(text: str, led: Ledger) -> tuple[int | None, str, str | None]:
    """'my Lloyds', 'my credit card', 'joint': by the user's own Account
    names, Banks and kinds. Two that fit: ask."""
    t = _norm(text)
    want: list[tuple[int, str]] = []
    for a in led.accounts():
        bank = a.bank.casefold()
        kind = a.kind.casefold().replace("_", " ")
        label = f"{a.bank} {a.name}".strip()
        if (
            (bank and re.search(rf"\bmy {re.escape(bank)}\b", t))
            or ("credit card" in t and "credit" in kind)
            or ("current account" in t and kind == "current")
            or ("savings" in t and "saving" in kind)
            or ("joint" in t and "joint" in a.name.casefold())
        ):
            want.append((a.id, label))
    if len(want) > 1:
        return None, "", "Which account: " + " or ".join(n for _, n in want) + "?"
    return (want[0][0], want[0][1], None) if want else (None, "", None)


def big_threshold(led: Ledger) -> int | None:
    """The user's own p90 single payment (nearest rank), in minor units."""
    out = sorted(
        int(-t.amount.scaleb(2))
        for t in led.transactions()
        if (c := t.categorisation) and c.kind == "Spending" and t.amount < 0
    )
    if not out:
        return None
    rank = -(-len(out) * (100 - BIG_SHARE) // 100)  # ceil
    return out[max(rank - 1, 0)]


def recurring(text: str, led: Ledger) -> tuple[tuple[int, ...], str] | None:
    """'subscriptions', 'bills': the detected recurring series (active)."""
    t = _norm(text)
    kind = "subscription" if re.search(r"\bsubscriptions?\b", t) else None
    kind = kind or ("bill" if re.search(r"\bbills?\b", t) else None)
    if kind is None:
        return None
    rows = [s for s in led.subscriptions() if s.kind == kind and s.status == "active"]
    if not rows:
        return None  # no series detected: the Bills or Subscriptions Category
    noun = kind if len(rows) == 1 else f"{kind}s"
    return tuple(sorted(s.merchant_id for s in rows)), f"{len(rows)} {noun}"


def income(text: str, led: Ledger) -> tuple[tuple[int, ...], str] | None:
    """'salary', 'income': Income Transactions' Merchants (never Transfers)."""
    t = _norm(text)
    if not re.search(r"\b(salary|income|pay ?check|wages)\b", t):
        return None
    counts: dict[int, int] = {}
    payers: dict[str, int] = {}  # an Income with no Merchant: by its payer text
    for x in led.transactions():
        c = x.categorisation
        if c and c.kind == "Income":
            if c.merchant_id is not None:
                counts[c.merchant_id] = counts.get(c.merchant_id, 0) + 1
            name = display_name(c.merchant or x.description)
            payers[name] = payers.get(name, 0) + 1
    salary = "salary" in t or "wages" in t
    if not counts:
        if salary and payers:
            # shortcut: no Merchant to select, so every Income counts; give
            # the payer an id if a second regular income turns up
            top = max(payers, key=lambda k: (payers[k], k))
            return (), f"Salary ({top})"
        return (), "Income"
    names = {m.id: display_name(m.name) for m in led.merchants()}
    if salary:
        top_id = max(counts, key=lambda k: (counts[k], -k))
        return (top_id,), f"Salary ({names.get(top_id, 'employer')})"
    return tuple(sorted(counts)), "Income"


_PEOPLE = re.compile(
    r"\b(rent|my (?:mum|mom|dad|sister|brother|partner|landlord|wife|husband))\b"
)


def person(
    text: str, memories: Sequence[Memory], known: Sequence[tuple[int, str]]
) -> tuple[tuple[int, ...], str, str | None] | None:
    """'rent', 'my mum': a Memory that names one of the user's Merchants;
    unknown, ask once (the answer can be remembered through a Proposal)."""
    m = _PEOPLE.search(_norm(text))
    if m is None:
        return None
    who = m[1].removeprefix("my ")
    for mem in memories:
        if re.search(rf"\b{re.escape(who)}\b", mem.text.casefold()):
            ids, name, _ = merchants(mem.text, known)
            if ids:
                return ids, f"{who.title()} -> {name}", None
    q = f"Who do you pay for {who}? Tell me the payee and I can remember it."
    return (), "", q


def resolve(
    text: str,
    today: date,
    led: Ledger,
    memories: Sequence[Memory] = (),
) -> Resolution:
    """Every reference in `text`, resolved against the user's own data."""
    r = Resolution()
    t = _norm(text)
    known = [(m.id, m.name) for m in led.merchants()]
    if (p := period(text, today)) is not None:
        r.period = p
        r.labels.append(p.label)
        period_values(r, p)
    if (pr := person(text, memories, known)) is not None:
        ids, label, q = pr
        r.merchant_ids, r.question, r.merchant_label = ids, q, label
        if label:
            r.labels.append(label)
        return r
    if (inc := income(text, led)) is not None:
        r.income = True
        r.merchant_ids, label = inc
        r.merchant_label = label
        r.labels.append(label)
    elif (rec := recurring(text, led)) is not None:
        r.merchant_ids, label = rec
        r.merchant_label = label
        r.labels.append(label)
        r.value("series", len(r.merchant_ids))
    else:
        cats, label = categories(text)
        r.categories = cats
        if cats:
            r.labels.append(label)
        rest = text
        for name in sorted(NAMES, key=len, reverse=True):  # not a Merchant again
            rest = re.sub(rf"\b{re.escape(name)}\b", " ", rest, flags=re.I)
        ids, mlabel, q = merchants(rest, known)
        if q:
            r.question = q
            return r
        if ids:
            r.merchant_ids, r.merchant_label = ids, mlabel
            r.labels.append(mlabel)
        else:
            if not r.categories:
                _semantic_term(text, r, led)
            if r.categories:
                _filed_elsewhere(r, led)
    aid, alabel, q = account(text, led)
    if q:
        r.question = q
        return r
    if aid is not None:
        r.account_id = aid
        r.labels.append(alabel)
    if re.search(r"\b(big|large|expensive|biggest)\b(?! ?(?:change|picture))", t):
        if (th := big_threshold(led)) is not None:
            r.threshold_minor = th
            r.value("threshold", Money(minor=th, currency=CURRENCY))
            r.value("share", Percent(value=Decimal(BIG_SHARE)))
            r.labels.append(f"over {fig(Money(minor=th))} (your top {BIG_SHARE}%)")
    if re.search(r"\b(usual|normal|average|typical)\b", t):
        r.compare = "median3"
        r.labels.append("vs your 3-month median")
        r.value("months", 3)
    m = re.search(r"\bin (euros?|eur|dollars?|usd)\b", t)
    if m:
        r.currency = "EUR" if m[1].startswith("eu") else "USD"
    return r


_PERIOD_WORDS = frozenset(
    "january february march april may june july august september october"
    " november december jan feb mar apr jun jul aug sep sept oct nov dec"
    " last this next since year years week weeks month months tax before"
    " previous past so far today one two three four six twelve during".split()
)
_FILLER = frozenset(
    "money total altogether overall spending spent spend paid pay cost costs"
    " me went go all big large expensive biggest purchases payments salary"
    " income wages usual normal average typical euros euro eur dollars usd"
    " account card".split()
)


def _semantic_term(text: str, r: Resolution, led: Ledger) -> None:
    """Words no resolver read, read by meaning: "forecourt" is Fuel."""
    left = unresolved(text, r)
    if not left:
        return
    phrase = " ".join(w for w in _norm(text).split() if w in left)
    found = semantic.term(led, phrase)
    if found is None or found.category is None:
        return  # shortcut: a term's Merchants alone aren't used yet, only its Category
    path = " > ".join(found.category)
    r.categories = (path,)
    r.labels.append(f"{found.category[1]} (for “{phrase}”)")
    r.read = frozenset(left)


def _filed_elsewhere(r: Resolution, led: Ledger) -> None:
    """The Category's own Merchants plus those filed elsewhere whose name sits
    nearest its description (calibrated: evals/semantic_resolution.py)."""
    idx = semantic.index(led)
    th = idx and semantic.thresholds(idx.model)
    if idx is None or th is None:
        return
    extra: dict[int, tuple[str, tuple[str, str] | None]] = {}
    for c in r.categories:
        top, _, sub = c.partition(" > ")
        if sub:
            for mid, name, own in idx.filed_elsewhere((top, sub), th):
                extra[mid] = (name, own)
    if not extra:
        return
    r.merchant_ids = tuple(sorted(extra))
    parts = [
        f"{display_name(n)} (filed under {' › '.join(own) if own else 'Uncategorised'})"
        for n, own in sorted(extra.values(), key=lambda x: x[0])
    ]
    r.note = "That includes " + ", ".join(parts) + "."
    r.labels.append("plus " + ", ".join(parts))


def unresolved(text: str, r: Resolution) -> set[str]:
    """Content words no resolver accounted for. The planner answers only
    when this is empty; anything else goes to the model, which may ask."""
    t = f" {_norm(text)} "
    for name in _BY_LENGTH:
        if set(NAMES[name]) & set(r.categories):
            t = re.sub(rf"(?<=\s){re.escape(name)}(?=[\s?.!,])", " ", t)
    for name in r.merchant_label.split(" + "):
        if name:
            t = t.replace(f" {name.casefold()}", " ")
    words = set(re.findall(r"[a-z][a-z']*", t))
    return {
        w
        for w in words - _STOP - _PERIOD_WORDS - _FILLER - r.read
        if len(w) > 2 and w.rstrip("s") not in _FILLER
    }
