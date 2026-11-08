"""`redact`: tool values and a question in, a placeholder payload out (ADR 0011).

Figures become {Pn}, Merchants {Mn}, exact dates and months {Dn} (with the month
kept as `YYYY-MM`), and user-made Categories {Cn}. Free text (the question, and
notes when the user opts in) is folded to printable ASCII, then every figure,
date, currency mark, account-like run and spelt-out number in it is cut, and
every Merchant it shares with the tool values becomes that Merchant's {Mn}.
A final check refuses to return a payload whose free text still names a value
from the map: a bug here fails closed instead of leaking."""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date

from finance_agent.gateway.types import (
    GENERIC_CATEGORIES,
    MAX_NOTE,
    MAX_QUESTION,
    PLACEHOLDER,
    Category,
    FieldName,
    GatewayPayload,
    Kind,
    Merchant,
    Money,
    Month,
    Note,
    PayloadFact,
    Percent,
    PlaceholderMap,
    Size,
    ToolFact,
    Value,
)


class LeakError(Exception):
    """The payload would have carried a real value. Never sent."""


# Cyrillic and Greek letters NFKC leaves alone but that read as Latin ones.
_LOOKALIKES = str.maketrans(
    "аеорсухіјѕӏԁԛԝАВЕКМНОРСТХІЈЅοαινκτρυΑΒΕΗΙΚΜΝΟΡΤΥΧ",
    "aeopcyxijsldqwABEKMHOPCTXIJSoaivktpuABEHIKMNOPTYX",
)


def fold(text: str) -> str:
    """Lower-case printable ASCII with single spaces. Any script's digits become
    ASCII digits and other numerals (十, circled and fraction forms) become "0",
    so they are still seen as figures; currency signs become "£", which the
    redactor cuts; everything else non-ASCII is dropped."""
    text = unicodedata.normalize("NFKC", text).casefold().translate(_LOOKALIKES)
    out: list[str] = []
    for ch in unicodedata.normalize("NFKD", text):
        if ch.isdecimal():
            out.append(str(unicodedata.decimal(ch)))
        elif ch.isnumeric():
            out.append("0")
        elif unicodedata.category(ch) == "Sc":
            out.append("£")
        elif ch.isspace():
            out.append(" ")
        elif " " < ch <= "~":
            out.append(ch)
    return " ".join("".join(out).split())


def name_pattern(name: str) -> str | None:
    """A pattern for `name` in folded text, as a whole word across any
    separators, in each of its casings (upper-casing "ı" gives "I", which folds
    differently); None when it has no letter or digit to match."""
    alts: set[str] = set()
    for variant in (name, name.upper(), name.lower(), name.title()):
        if words := re.findall(r"[a-z0-9]+", fold(variant)):
            alts.add(r"[^a-z0-9]{0,3}".join(map(re.escape, words)))
    if not alts:
        return None
    body = "|".join(sorted(alts, key=len, reverse=True))
    return rf"(?<![a-z0-9])(?:{body})(?![a-z0-9])"


_MONTHS = (
    "january february march april may june july august september october"
    " november december"
).split()
_MON_NAMES = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)


def _mon(n: int) -> str:
    return rf"(?P<mon{n}>{_MON_NAMES})"


NUMBER_WORDS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
    "|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty"
    "|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|billion|grand"
)
_NB = r"(?<![a-z0-9])"  # no letter or digit before (text is folded: lower case)
_NA = r"(?![a-z0-9])"  # nor after
_DIGITY = r"[a-z0-9]*[0-9][a-z0-9]*"  # a word with a digit in it
_Y = "[0-9]{4}"
_DAY = r"[0-9]{1,2}(?:st|nd|rd|th)?"

# Free-text rules, most specific first. Each is one alternation group.
_RULES = (
    ("brace", r"[{}]"),  # a user's own "{P1}" must not read as a placeholder
    ("ymd", rf"{_NB}(?P<y1>{_Y})[-/.](?P<m1>[0-9]{{1,2}})[-/.][0-9]{{1,2}}{_NA}"),
    ("dmy", rf"{_NB}[0-9]{{1,2}}[-/.](?P<m2>[0-9]{{1,2}})[-/.](?P<y2>{_Y}){_NA}"),
    ("dmony", rf"{_NB}{_DAY}\s+(?:of\s+)?{_mon(3)}\.?,?\s+(?P<y3>{_Y}){_NA}"),
    ("monyd", rf"{_NB}{_mon(4)}\.?\s+{_DAY},?\s+(?P<y4>{_Y}){_NA}"),
    ("ym", rf"{_NB}(?P<y5>{_Y})[-/](?P<m5>[0-9]{{1,2}}){_NA}"),
    ("my", rf"{_NB}(?P<m6>[0-9]{{1,2}})[-/](?P<y6>{_Y}){_NA}"),
    ("mony", rf"{_NB}{_mon(7)}\.?,?\s+(?P<y7>{_Y}){_NA}"),
    # IBAN, letter groups and all: its bank code names the bank
    (
        "iban",
        rf"{_NB}[a-z]{{2}}[0-9]{{2}}(?:\s?[a-z0-9]{{4}}){{2,8}}(?:\s?[a-z0-9]{{1,4}})?",
    ),
    # any run of digit-bearing words joined by single separators: amounts,
    # sort codes, card and account numbers, "£12k", "50p", "12,345.67"
    ("figure", rf"[-+]?(?:£+\s?)?{_DIGITY}(?:[ ,.'/:_-]{_DIGITY})*%?(?:\s?£+)?"),
    ("currency", r"£+"),
    (
        "words",
        rf"\b(?:{NUMBER_WORDS})(?:[\s-]+(?:and[\s-]+)?(?:{NUMBER_WORDS}))*\b",
    ),
)


def _month_name(name: str) -> int:
    return next(i for i, m in enumerate(_MONTHS, 1) if m.startswith(name.lower()[:3]))


def _month_text(year: str, month: int) -> str | None:
    return f"{int(year):04d}-{month:02d}" if 1 <= month <= 12 else None


class _Redactor:
    def __init__(self, merchant_names: dict[str, str]) -> None:
        # placeholder -> pattern, longest name first so "Tesco Express" beats "Tesco"
        pats = {ph: p for ph, n in merchant_names.items() if (p := name_pattern(n))}
        self._merchants = sorted(pats.items(), key=lambda kv: -len(kv[1]))
        alts = [f"(?P<mer{i}>{p})" for i, (_, p) in enumerate(self._merchants)]
        alts += [f"(?P<{name}>{p})" for name, p in _RULES]
        self._re = re.compile("|".join(alts), re.IGNORECASE)

    def _sub(self, m: re.Match[str]) -> str:
        kind = m.lastgroup or ""
        if kind.startswith("mer"):
            return self._merchants[int(kind[3:])][0]
        month: str | None = None
        for n in "1256":
            if m[f"y{n}"]:
                month = _month_text(m[f"y{n}"], int(m[f"m{n}"]))
        for n in "347":
            if m[f"y{n}"]:
                month = _month_text(m[f"y{n}"], _month_name(m[f"mon{n}"]))
        if month:
            return month  # months may leave; days never do
        if kind in ("brace", "currency"):
            return ""
        if kind == "words" and m[0].lower() == "one":
            return m[0]  # "which one", "no one": not a figure
        return "#"  # a figure, a day or an account number: cut

    def text(self, raw: str, limit: int) -> str:
        out = self._re.sub(self._sub, fold(raw)[: limit * 2])
        out = " ".join(out.split())
        if len(out) > limit:  # cut at a space so no placeholder is split
            out = re.sub(r"\{[^}]*$", "", out[:limit].rsplit(" ", 1)[0])
        return out


def _money_size(minor: int) -> Size:
    pounds = abs(minor) // 100
    return (
        "tiny"
        if pounds < 10
        else "small"
        if pounds < 100
        else "medium"
        if pounds < 1000
        else "large"
        if pounds < 10000
        else "very large"
    )


def _key(value: Value) -> object:
    """Values that are equal share one placeholder; names match case-blind."""
    if isinstance(value, Merchant | Category):
        return (type(value).__name__, fold(value.name).casefold())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, int):
        return ("count", value)
    return (type(value).__name__, value.model_dump_json())


def redact(
    question: str,
    tool_values: Iterable[ToolFact],
    *,
    include_notes: bool = False,
    intent: str = "answer",
) -> tuple[GatewayPayload, PlaceholderMap]:
    """Build the payload a cloud model may see, and the map to fill its reply."""
    facts = tuple(tool_values)
    entries: dict[str, Value] = {}
    seen: dict[object, str] = {}
    counts = {"P": 0, "M": 0, "D": 0, "C": 0}

    def placeholder(prefix: str, value: Value) -> str:
        key = _key(value)
        if key not in seen:
            counts[prefix] += 1
            seen[key] = f"{{{prefix}{counts[prefix]}}}"
            entries[seen[key]] = value
        return seen[key]

    # Names first: a Category that names a Merchant is not generic any more, and
    # the free text needs every name's placeholder before it is redacted.
    names: dict[str, str] = {}
    for f in facts:
        if isinstance(f.value, Merchant):
            names[placeholder("M", f.value)] = f.value.name
    pats = [p for n in names.values() if (p := name_pattern(n))]
    names_merchant = re.compile("|".join(pats), re.IGNORECASE) if pats else None

    def generic(c: Category) -> bool:
        return c.name in GENERIC_CATEGORIES and not (
            names_merchant and names_merchant.search(fold(c.name))
        )

    for f in facts:
        if isinstance(f.value, Category) and not generic(f.value):
            names[placeholder("C", f.value)] = f.value.name

    out: list[PayloadFact] = []
    notes: list[str] = []
    redactor = _Redactor(names)
    for f in facts:
        v = f.value
        kind: Kind
        size: Size | None = None
        month: str | None = None
        category: str | None = None
        ph: str | None
        if isinstance(v, Note):
            if include_notes:
                notes.append(redactor.text(v.text, MAX_NOTE))
            continue
        if isinstance(v, Merchant):
            kind, ph = "merchant", placeholder("M", v)
        elif isinstance(v, Category):
            kind = "category"
            ph, category = (None, v.name) if generic(v) else (placeholder("C", v), None)
        elif isinstance(v, Money):
            kind, ph, size = "money", placeholder("P", v), _money_size(v.minor)
        elif isinstance(v, Percent):
            kind, ph = "percent", placeholder("P", v)
            size = (
                "small"
                if abs(v.value) < 10
                else "medium"
                if abs(v.value) < 50
                else "large"
            )
        elif isinstance(v, Month):
            kind, ph, month = (
                "month",
                placeholder("D", v),
                f"{v.year:04d}-{v.month:02d}",
            )
        elif isinstance(v, date):
            kind, ph, month = "date", placeholder("D", v), f"{v.year:04d}-{v.month:02d}"
        else:
            kind, ph, size = (
                "count",
                placeholder("P", v),
                "few" if abs(v) < 10 else "many",
            )
        field: FieldName = f.field
        out.append(
            PayloadFact(
                placeholder=ph,
                kind=kind,
                field=field,
                size=size,
                month=month,
                category=category,
            )
        )

    payload = GatewayPayload(
        intent=intent,
        question=redactor.text(question, MAX_QUESTION),
        facts=tuple(out),
        notes=tuple(notes),
    )
    placeholders = PlaceholderMap(entries=tuple(entries.items()))
    _refuse_leaks(payload, placeholders)
    return payload, placeholders


def _refuse_leaks(payload: GatewayPayload, names: PlaceholderMap) -> None:
    """The schema already keeps figures out of free text; this keeps names out."""
    free = [payload.question, *payload.notes]
    free += [f.category for f in payload.facts if f.category]
    text = " ".join(fold(PLACEHOLDER.sub(" ", t)) for t in free)  # {M1} is no "m1"
    for ph in names.placeholders():
        v = names[ph]
        if isinstance(v, Merchant | Category) and (p := name_pattern(v.name)):
            if re.search(p, text, re.IGNORECASE):
                raise LeakError(f"payload names {ph}")
