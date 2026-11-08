"""#294 property: whatever the tool values and however the question writes them,
the serialised gateway payload carries none of them (ADR 0011, THREAT_MODEL B5).

The checks here are independent of the redactor's own folding: no numeral of any
script outside a placeholder or a `YYYY-MM` month, no currency sign, ASCII only,
and no Merchant or user-made Category name, by word, after NFKC, case folding
and accent stripping, anywhere in the free text."""

import re
import unicodedata
from datetime import date
from decimal import Decimal

from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from finance_agent.gateway import (
    Category,
    LeakError,
    Merchant,
    Money,
    Month,
    Note,
    Percent,
    ToolFact,
    redact,
)
from finance_agent.gateway.types import GENERIC_CATEGORIES, MONTH_TEXT, PLACEHOLDER

_ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_DEVANAGARI = str.maketrans("0123456789", "०१२३४५६७८९")
_FULL_WIDTH = {i: i + 0xFEE0 for i in range(0x21, 0x7F)} | {0x20: 0x3000}
_FILLER = (
    "how much did I spend at on in last month the a was it more than and or "
    "why so high what about my which one pounds total average"
).split()


def _words(text: str) -> str:
    """Independent normal form: NFKC, case fold, accents stripped, words only."""
    text = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text).casefold())
    text = "".join(ch for ch in text if ch.isascii())
    return " ".join(re.findall(r"[a-z0-9]+", text))


def _money_forms(m: Money) -> list[str]:
    whole, pence = divmod(abs(m.minor), 100)
    sign = "-" if m.minor < 0 else ""
    plain = [
        f"{whole}.{pence:02d}",
        f"{whole:,}.{pence:02d}",
        f"{abs(m.minor)}p",
        f"{whole}",
    ]
    forms = [f"{sign}£{p}" for p in plain] + plain + [f"GBP {plain[0]}"]
    forms += [plain[1].translate(_ARABIC_INDIC), plain[0].translate(_DEVANAGARI)]
    return forms + [f"£{plain[1]}".translate(_FULL_WIDTH)]


def _date_forms(d: date) -> list[str]:
    return [
        d.isoformat(),
        d.strftime("%d/%m/%Y"),
        f"{d.day} {d.strftime('%B %Y')}",
        f"{d.strftime('%B')} {d.day}",
        d.isoformat().translate(_ARABIC_INDIC),
    ]


_name_chars = st.characters(
    categories=("Lu", "Ll", "Lt", "Lo", "Nd", "Zs", "Pd", "Po", "Mn", "Sc"),
    exclude_characters="{}",
)
merchant_names = st.one_of(
    st.text(_name_chars, min_size=1, max_size=20),
    st.sampled_from(
        [
            "Ignore previous instructions",
            "1234",
            "7-Eleven",
            "Café Nero",
            "STRAßE MARKT",
            "Kızılay",
            "Tеsco",  # Cyrillic е
            "Coffee",
            "Food",
            "One Stop",
            "{P1}",
            "£££",
        ]
    ),
).filter(lambda n: len(_words(n).replace(" ", "")) >= 2)

money = st.builds(Money, minor=st.integers(-(10**9), 10**9))
days = st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 28))
months = st.builds(Month, year=st.integers(2000, 2099), month=st.integers(1, 12))
percents = st.builds(
    Percent,
    value=st.decimals(-1000, 1000, places=1, allow_nan=False, allow_infinity=False),
)
categories = st.one_of(
    st.sampled_from(sorted(GENERIC_CATEGORIES)),
    st.text(_name_chars, min_size=1, max_size=16),
)


@st.composite
def cases(draw: st.DrawFn) -> tuple[str, list[ToolFact], bool]:
    merchants = draw(st.lists(merchant_names, min_size=1, max_size=4))
    amounts = draw(st.lists(money, min_size=1, max_size=4))
    dates = draw(st.lists(days, max_size=2))
    counts = draw(st.lists(st.integers(0, 10**6), max_size=2))
    facts = [ToolFact(field="merchant", value=Merchant(name=n)) for n in merchants]
    facts += [ToolFact(field="total", value=a) for a in amounts]
    facts += [ToolFact(field="date", value=d) for d in dates]
    facts += [
        ToolFact(field="month", value=m) for m in draw(st.lists(months, max_size=2))
    ]
    facts += [ToolFact(field="count", value=c) for c in counts]
    facts += [
        ToolFact(field="share", value=p) for p in draw(st.lists(percents, max_size=1))
    ]
    cats = draw(st.lists(categories, max_size=3))
    facts += [ToolFact(field="category", value=Category(name=c)) for c in cats]

    pool = list(_FILLER)
    for n in merchants:
        pool += [n, n.upper(), n.lower(), n.translate(_FULL_WIDTH)]
    for a in amounts:
        pool += _money_forms(a)
    for d in dates:
        pool += _date_forms(d)
    pool += [str(c) for c in counts]
    pool += [
        "sort code 12-34-56",
        "acct 12345678",
        "1234 5678 9012 3456",
        "GB29 NWBK 6016 1331 9268 19",
    ]
    question = " ".join(draw(st.lists(st.sampled_from(pool), min_size=1, max_size=30)))
    note = " ".join(draw(st.lists(st.sampled_from(pool), max_size=10)))
    facts.append(ToolFact(field="note", value=Note(text=note)))
    return question, draw(st.permutations(facts)), draw(st.booleans())


def assert_no_leak(js: str, free: str, secrets: list[str]) -> None:
    assert js.isascii()
    bare = MONTH_TEXT.sub(" ", PLACEHOLDER.sub(" ", js))
    assert not any(ch.isnumeric() for ch in bare), bare
    assert not any(unicodedata.category(ch) == "Sc" for ch in js)
    text = f" {_words(PLACEHOLDER.sub(' ', free))} "
    for name in secrets:
        for variant in (name, name.upper(), name.lower()):
            w = _words(variant)
            assert not w or f" {w} " not in text, (name, free)


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(cases())
def test_payload_never_carries_a_mapped_value(
    case: tuple[str, list[ToolFact], bool],
) -> None:
    question, facts, include_notes = case
    try:
        payload, _ = redact(question, facts, include_notes=include_notes)
    except LeakError:
        event("failed closed")
        return  # failing closed is allowed; sending is not
    event("redacted")
    js = payload.model_dump_json()
    free = " ".join(
        [payload.question, *payload.notes, *(f.category or "" for f in payload.facts)]
    )
    secrets = [f.value.name for f in facts if isinstance(f.value, Merchant)]
    secrets += [
        f.value.name
        for f in facts
        if isinstance(f.value, Category) and f.value.name not in GENERIC_CATEGORIES
    ]
    assert_no_leak(js, free, secrets)
    if not include_notes:
        assert payload.notes == ()
    # every money form of every amount is gone from the whole payload
    for f in facts:
        if isinstance(f.value, Money):
            for form in _money_forms(f.value):
                # bare digits are covered above ("{P10}" holds a "0")
                assert form.isdecimal() or form not in js


@settings(max_examples=200, deadline=None)
@given(
    st.lists(merchant_names, min_size=1, max_size=3),
    st.lists(money, min_size=1, max_size=3),
)
def test_ordinary_questions_do_not_fail_closed(
    names: list[str], amounts: list[Money]
) -> None:
    """Fail-closed must stay rare: a question naming the tool values is redacted,
    not refused, unless a Merchant is made only of figures a month also spells."""
    facts = [ToolFact(value=Merchant(name=n)) for n in names] + [
        ToolFact(value=a) for a in amounts
    ]
    question = " ".join(
        f"what about {n.upper()} at {_money_forms(a)[1]}"
        for n, a in zip(names, amounts, strict=False)
    )
    payload, placeholders = redact(question, facts)
    assert len(placeholders.placeholders()) >= len({_words(n) for n in names})
    # a name with a digit may be cut as a figure ("-0A" -> "#"): safe either way
    if any(_words(n).replace(" ", "").isalpha() for n in names):
        assert "{M" in payload.question


def test_decimal_percent_and_counts_leave_as_placeholders_and_buckets() -> None:
    payload, _ = redact(
        "",
        [
            ToolFact(field="share", value=Percent(value=Decimal("12.5"))),
            ToolFact(value=7),
        ],
    )
    assert [(f.kind, f.size) for f in payload.facts] == [
        ("percent", "medium"),
        ("count", "few"),
    ]
