# synthetic
"""The grounding check (#137, ADR 0007): every £ figure, percentage and date in a
reply must match a value from this turn's tool results."""

import time
from datetime import date
from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from finance_agent.assistant.grounding import (
    Figure,
    Money,
    Month,
    Percent,
    ToolValue,
    check_grounding,
    render_fallback,
)

TODAY = date(2026, 3, 12)  # a Thursday


def _v(evidence_id: str, value: Figure) -> ToolValue:
    return ToolValue(evidence_id=evidence_id, label="synthetic", value=value)


def _check(reply: str, *values: ToolValue):
    return check_grounding(reply, values, today=TODAY)


def test_matching_amount_is_grounded_with_its_evidence_id():
    result = _check("You spent £12.35 on coffee.", _v("ev-a", Money(minor=1235)))
    assert result.grounded
    assert result.evidence_ids == ("ev-a",)
    assert result.offending == ()


def test_near_miss_amount_is_rejected_with_its_span():
    reply = "You spent £12.53 on coffee."
    result = _check(reply, _v("ev-a", Money(minor=1235)))
    assert not result.grounded
    assert [s.text for s in result.offending] == ["£12.53"]
    span = result.offending[0]
    assert reply[span.start : span.end] == "£12.53"


def test_percent_and_dates_ground():
    result = _check(
        "Up 12% since 3 March 2026.",
        _v("ev-p", Percent(value=Decimal("12"))),
        _v("ev-d", date(2026, 3, 3)),
    )
    assert result.grounded
    assert result.evidence_ids == ("ev-d", "ev-p")


FORMATS = [
    ("£1,234.50", Money(minor=123450)),
    ("£1234.5", Money(minor=123450)),
    ("£ 1234.50", Money(minor=123450)),
    ("1.2k", Money(minor=120000)),
    ("£1.2k", Money(minor=120000)),
    ("£3 million", Money(minor=300000000)),
    ("−£40", Money(minor=-4000)),
    ("-£40.00", Money(minor=-4000)),
    ("£-40", Money(minor=-4000)),
    ("40p", Money(minor=40)),
    ("40 pence", Money(minor=40)),
    ("40 GBP", Money(minor=4000)),
    ("GBP 40", Money(minor=4000)),
    ("40 pounds", Money(minor=4000)),
    ("$12.35", Money(minor=1235, currency="USD")),
    ("€12.35", Money(minor=1235, currency="EUR")),
    ("12%", Percent(value=Decimal("12"))),
    ("12.50%", Percent(value=Decimal("12.5"))),
    ("12 percent", Percent(value=Decimal("12"))),
    ("12 per cent", Percent(value=Decimal("12"))),
    ("-3%", Percent(value=Decimal("-3"))),
    ("3 March", date(2026, 3, 3)),
    ("3rd March 2026", date(2026, 3, 3)),
    ("3 Mar 2026", date(2026, 3, 3)),
    ("March 3, 2026", date(2026, 3, 3)),
    ("03/03/2026", date(2026, 3, 3)),
    ("2026-03-03", date(2026, 3, 3)),
    ("March 2026", Month(year=2026, month=3)),
    ("yesterday", date(2026, 3, 11)),
    ("today", date(2026, 3, 12)),
    ("tomorrow", date(2026, 3, 13)),
    ("last Tuesday", date(2026, 3, 10)),
    ("last Thursday", date(2026, 3, 5)),
    ("next Monday", date(2026, 3, 16)),
    ("3 subscriptions", 3),
    ("**£12.35**", Money(minor=1235)),
    ("£１２.３５", Money(minor=1235)),
    ("\n- £40", Money(minor=4000)),  # a Markdown bullet is not a minus sign
]


@pytest.mark.parametrize(("text", "value"), FORMATS, ids=[f[0] for f in FORMATS])
def test_supported_format_grounds_against_its_value(text: str, value: Figure):
    result = _check(f"Note: {text}.", _v("ev", value))
    assert result.grounded, result.offending
    assert result.evidence_ids == ("ev",)


# (reply text, the tool value it must NOT be grounded by)
ADVERSARIAL = [
    ("£12.53", Money(minor=1235)),  # near miss: transposed
    ("£12.36", Money(minor=1235)),
    ("£12.4", Money(minor=1235)),  # rounding the tool did not do
    ("£12", Money(minor=1235)),
    ("£12.35k", Money(minor=1235)),  # unit trick
    ("12.35k", Money(minor=1235)),
    ("£12.35 million", Money(minor=1235)),
    ("1235p", Money(minor=123500)),  # pence read as pounds
    ("£12.355", Money(minor=1235)),  # finer than a penny
    ("twelve pounds", Money(minor=1200)),  # figures in words
    ("twelve pounds thirty-five", Money(minor=1235)),
    ("£twelve", Money(minor=1200)),
    ("ten percent", Percent(value=Decimal("10"))),
    ("£1​2.35", Money(minor=1200)),  # zero-width split hides "£12.35"
    ("£1**2**.35", Money(minor=1200)),  # Markdown split
    ("£12.<b>35</b>", Money(minor=1235)),
    ("$12.35", Money(minor=1235)),  # another currency
    ("€12.35", Money(minor=1235)),
    ("12.35 USD", Money(minor=1235)),
    ("£12.35", Money(minor=1235, currency="USD")),
    ("−£40", Money(minor=4000)),  # sign confusion
    ("£40", Money(minor=-4000)),
    ("-12%", Percent(value=Decimal("12"))),
    ("12%", Money(minor=1200)),  # wrong kind
    ("£12", Percent(value=Decimal("12"))),
    ("12", Money(minor=1200)),
    ("3 March", date(2026, 3, 4)),
    ("03/04/2026", date(2026, 3, 4)),  # US order is not read as 4 March
    ("3/3/26", date(2026, 3, 3)),  # two-number year
    ("31/02/2026", date(2026, 3, 3)),  # no such day
    ("March 2026", date(2026, 3, 1)),  # a month is not a day in it
    ("3 March 2025", date(2026, 3, 3)),
    ("yesterday", date(2026, 3, 12)),
    ("last Tuesday", date(2026, 3, 3)),
    ("12.5", Percent(value=Decimal("12.5"))),  # bare decimal: never grounded
    ("10:30", 10),
]


@pytest.mark.parametrize(
    ("text", "value"), ADVERSARIAL, ids=[a[0] for a in ADVERSARIAL]
)
def test_adversarial_figure_is_rejected(text: str, value: Figure):
    assert not _check(f"Note: {text}.", _v("ev", value)).grounded


@pytest.mark.parametrize(
    "reply", ["one " * 50_000 + "pounds", "twenty-" * 30_000, "1" * 200_000]
)
def test_hostile_long_reply_is_checked_in_linear_time(reply: str):
    started = time.perf_counter()
    _check(reply)
    assert time.perf_counter() - started < 5  # was minutes when quadratic


# --- property tests -------------------------------------------------------

_SYMBOL = {"GBP": "£", "USD": "$", "EUR": "€"}
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]  # fmt: skip
_WEEKDAY_NAMES = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]  # fmt: skip

figures = st.one_of(
    st.builds(
        Money,
        minor=st.integers(-(10**9), 10**9),
        currency=st.sampled_from(["GBP", "GBP", "USD", "EUR"]),
    ),
    st.builds(
        Percent, value=st.integers(-(10**5), 10**5).map(lambda n: Decimal(n).scaleb(-2))
    ),
    st.builds(Month, year=st.integers(2000, 2099), month=st.integers(1, 12)),
    st.dates(date(2000, 1, 1), date(2099, 12, 31)),
    st.integers(TODAY.toordinal() - 8, TODAY.toordinal() + 8).map(date.fromordinal),
    st.integers(0, 10**6),
)


def _money_forms(m: Money) -> list[str]:
    pounds, pence = divmod(abs(m.minor), 100)
    sym, sign = _SYMBOL[m.currency], "-" if m.minor < 0 else ""
    numbers = [f"{pounds:,}.{pence:02d}", f"{pounds}.{pence:02d}"]
    if pence == 0:
        numbers.append(str(pounds))
    elif pence % 10 == 0:
        numbers.append(f"{pounds}.{pence // 10}")
    forms = [f"{s}{sym}{n}" for n in numbers for s in {sign, sign.replace("-", "−")}]
    forms += [f"{sym}{sign}{n}" for n in numbers]
    forms += [f"{sign}{numbers[0]} {m.currency}", f"{m.currency} {sign}{numbers[1]}"]
    if m.currency == "GBP" and 0 < abs(m.minor) < 100:
        forms.append(f"{sign}{abs(m.minor)}p")
    if m.currency == "GBP" and pence == 0 and pounds % 100 == 0 and pounds:
        k = format(Decimal(pounds).scaleb(-3).normalize(), "f")
        forms += [f"{sign}£{k}k", f"{sign}{k}k"]
    return forms


def _date_forms(d: date, partial: bool) -> list[str]:
    name = _MONTH_NAMES[d.month - 1]
    forms = [
        f"{d.day} {name} {d.year}",
        f"{d.day} {name[:3]} {d.year}",
        f"{name} {d.day}, {d.year}",
        f"{d.day:02d}/{d.month:02d}/{d.year}",
        f"{d.day}/{d.month}/{d.year}",
        d.isoformat(),
    ]
    if partial:
        forms.append(f"{d.day} {name}")
    delta = d.toordinal() - TODAY.toordinal()
    forms += {-1: ["yesterday"], 0: ["today"], 1: ["tomorrow"]}.get(delta, [])
    weekday = _WEEKDAY_NAMES[d.weekday()]
    if -7 <= delta <= -1:
        forms.append(f"last {weekday}")
    if 1 <= delta <= 7:
        forms.append(f"next {weekday}")
    return forms


def _forms(value: Figure, partial: bool = True) -> list[str]:
    """Every supported way of writing `value` exactly."""
    if isinstance(value, Money):
        return _money_forms(value)
    if isinstance(value, Percent):
        v = format(value.value, "f")
        return [f"{v}%", f"{v} percent", f"{v} per cent"]
    if isinstance(value, Month):
        name = _MONTH_NAMES[value.month - 1]
        return [f"{name} {value.year}", f"{name[:3]} {value.year}"]
    if isinstance(value, date):
        return _date_forms(value, partial)
    return [str(value)]


_SEPARATORS = st.sampled_from([" and ", ", then ", ". Also ", " | ", "\n- ", " (see) "])


@st.composite
def _reply(
    draw: st.DrawFn, values: list[Figure], partial: bool = True
) -> tuple[str, list[int]]:
    """A reply written only from `values`, and where each one starts."""
    parts: list[str] = []
    starts: list[int] = []
    length = len("Summary: ")
    for value in values:
        text = draw(st.sampled_from(_forms(value, partial)))
        if draw(st.booleans()):
            text = f"**{text}**"
        sep = draw(_SEPARATORS)
        starts.append(length)
        parts.append(text + sep)
        length += len(text) + len(sep)
    return "Summary: " + "".join(parts), starts


@given(data=st.data(), values=st.lists(figures, min_size=1, max_size=6))
def test_reply_made_only_of_tool_values_is_grounded(
    data: st.DataObject, values: list[Figure]
):
    tool = [_v(f"ev-{i}", v) for i, v in enumerate(values)]
    reply, _ = data.draw(_reply(values))
    result = _check(reply, *tool)
    assert result.grounded, (reply, result.offending)
    assert set(result.evidence_ids) <= {t.evidence_id for t in tool}


@given(
    data=st.data(),
    values=st.lists(figures, max_size=5),
    extra=figures,
    position=st.integers(0, 5),
)
def test_reply_with_any_figure_absent_from_tool_values_is_rejected(
    data: st.DataObject, values: list[Figure], extra: Figure, position: int
):
    assume(all(type(v) is not type(extra) or v != extra for v in values))
    position = min(position, len(values))
    written = [*values[:position], extra, *values[position:]]
    reply, starts = data.draw(_reply(written, partial=False))
    result = _check(reply, *(_v(f"ev-{i}", v) for i, v in enumerate(values)))
    assert not result.grounded
    # only the extra figure is flagged, every tool value still grounds
    extra_start = starts[position]
    extra_end = starts[position + 1] if position + 1 < len(starts) else len(reply)
    assert all(extra_start <= s.start < extra_end for s in result.offending), (
        reply,
        result.offending,
    )


# --- deterministic fallback -------------------------------------------------


def test_fallback_is_a_table_of_every_tool_value():
    tool = [
        ToolValue(
            evidence_id="ev-1", label="Groceries | October", value=Money(minor=-4000)
        ),
        ToolValue(
            evidence_id="ev-2",
            label="Change\nvs September",
            value=Percent(value=Decimal("12.5")),
        ),
        ToolValue(evidence_id="ev-3", label="Next renewal", value=date(2026, 3, 3)),
        ToolValue(evidence_id="ev-4", label="Month", value=Month(year=2026, month=3)),
        ToolValue(evidence_id="ev-5", label="Subscriptions", value=3),
        ToolValue(
            evidence_id="ev-6",
            label="Card fee",
            value=Money(minor=123456, currency="USD"),
        ),
    ]
    lines = render_fallback(tool).splitlines()
    assert lines[2:] == [
        "| Item | Value | Evidence |",
        "| --- | --- | --- |",
        "| Groceries \\| October | -£40.00 | ev-1 |",
        "| Change vs September | 12.5% | ev-2 |",
        "| Next renewal | 3 March 2026 | ev-3 |",
        "| Month | March 2026 | ev-4 |",
        "| Subscriptions | 3 | ev-5 |",
        "| Card fee | $1,234.56 | ev-6 |",
    ]


def test_fallback_with_no_values_says_so_without_figures():
    text = render_fallback([])
    assert check_grounding(text, [], today=TODAY).grounded


@given(values=st.lists(figures, min_size=1, max_size=6))
def test_fallback_figures_pass_the_grounding_check(values: list[Figure]):
    tool = [_v(f"ev{i}", v) for i, v in enumerate(values)]
    cells = [row.split(" | ")[1] for row in render_fallback(tool).splitlines()[4:]]
    assert check_grounding(" and ".join(cells), tool, today=TODAY).grounded
