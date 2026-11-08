"""The playbook engine (#254): signals from rows, conditions, slot filling."""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from finance_agent.engine import playbook
from finance_agent.engine.detectors import Row

TODAY = date(2026, 5, 10)
SUBS = ("Subscriptions", "Streaming")
RENT = ("Bills", "Rent or mortgage")


def _month(m: int, day: int = 3) -> date:  # m months before TODAY's month
    y, mo = divmod(TODAY.year * 12 + TODAY.month - 1 - m, 12)
    return date(y, mo + 1, day)


def _rows(spec: list[tuple[int, tuple[str, str], int]]) -> list[Row]:
    return [
        Row(i, _month(m), "FICTIONAL CO", cat, -pence)
        for i, (m, cat, pence) in enumerate(spec, 1)
    ]


# --- signals --------------------------------------------------------------


def test_emergency_fund_months_is_savings_over_three_months_of_essentials():
    # rent 1000.00 in each of the last three complete months; history from 5 back
    rows = _rows([(m, RENT, 100_000) for m in (5, 4, 3, 2, 1)])
    s = playbook.signals(rows, savings_balance=290_000, today=TODAY)
    assert s["essential_monthly_spend"] == 100_000
    assert s["savings_balance"] == 290_000
    assert s["emergency_fund_months"] == Decimal("2.9")


def test_emergency_fund_months_rounds_down_so_shown_matches_condition():
    rows = _rows([(m, RENT, 100_000) for m in (5, 4, 3, 2, 1)])
    s = playbook.signals(rows, savings_balance=299_999, today=TODAY)
    assert s["emergency_fund_months"] == Decimal("2.9")


def test_partial_first_month_and_this_month_do_not_count():
    # only two complete months after the first (partial) month: insufficient
    rows = _rows([(m, RENT, 100_000) for m in (3, 2, 1, 0)])
    s = playbook.signals(rows, savings_balance=0, today=TODAY)
    assert s["essential_monthly_spend"] is None
    assert s["emergency_fund_months"] is None


def test_no_rows_gives_no_figures():
    s = playbook.signals([], savings_balance=10_000, today=TODAY)
    assert set(s) == set(playbook.SIGNALS)
    assert all(s[k] is None for k in s if k != "savings_balance")


def test_subscription_rise_streak_counts_consecutive_monthly_rises():
    rows = _rows(
        [(5, SUBS, 500), (4, SUBS, 500), (3, SUBS, 600), (2, SUBS, 700), (1, SUBS, 900)]
    )
    s = playbook.signals(rows, savings_balance=None, today=TODAY)
    assert s["subscription_rise_streak"] == 3
    assert s["subscription_spend_last_month"] == 900
    assert s["subscription_spend_rise"] == 400


def test_a_flat_month_breaks_the_streak():
    rows = _rows(
        [(5, SUBS, 500), (4, SUBS, 600), (3, SUBS, 700), (2, SUBS, 700), (1, SUBS, 800)]
    )
    s = playbook.signals(rows, savings_balance=None, today=TODAY)
    assert s["subscription_rise_streak"] == 1
    assert s["subscription_spend_rise"] == 100


def test_a_month_with_no_subscriptions_counts_as_zero():
    rows = _rows([(5, RENT, 1), (2, SUBS, 500), (1, SUBS, 600)])
    s = playbook.signals(rows, savings_balance=None, today=TODAY)
    assert s["subscription_rise_streak"] == 2
    assert s["subscription_spend_rise"] == 600


# --- conditions -----------------------------------------------------------


def test_missing_signal_never_satisfies_a_condition():
    when = playbook.parse_condition(
        {"any": [{"signal": "emergency_fund_months", "op": "<", "value": 3}]}
    )
    assert not playbook.holds(when, {"emergency_fund_months": None})
    assert playbook.holds(when, {"emergency_fund_months": Decimal("2.9")})


@pytest.mark.parametrize(
    "raw",
    [
        {"signal": "made_up_signal", "op": "<", "value": 3},
        {"signal": "emergency_fund_months", "op": "!=", "value": 3},
        {"signal": "emergency_fund_months", "op": "<", "value": "3"},
        {"signal": "emergency_fund_months", "op": "<", "value": True},
        {"signal": "emergency_fund_months", "op": "<", "value": 3.0},
        {"signal": "subscription_rise_streak", "op": ">=", "value": Decimal("2.5")},
        {"all": []},
        {"all": [{"signal": "savings_balance", "op": "<", "value": 1}], "x": 1},
    ],
)
def test_bad_conditions_fail_validation(raw: object):
    with pytest.raises(playbook.PlaybookError):
        playbook.parse_condition(raw)


# --- rendering ------------------------------------------------------------


def _card(body: str, when: object | None = None) -> dict[str, object]:
    return {
        "id": "test-card",
        "version": 1,
        "topic": "emergency_fund",
        "principles": ["Keep a cushion for surprises."],
        "when": when
        or {"all": [{"signal": "emergency_fund_months", "op": "<", "value": 3}]},
        "variants": {
            "gentle": {"title": "A cushion", "body": body},
            "blunt": {"title": "Build a cushion", "body": body},
        },
    }


FACTS = {
    "isa_allowance": playbook.Fact(
        "isa_allowance",
        "ISA allowance",
        2_000_000,
        "money",
        "https://www.gov.uk/individual-savings-accounts",
        date(2026, 4, 10),
    )
}


def test_render_fills_slots_and_shows_facts_with_checked_on():
    card = playbook.parse_card(
        _card(
            "You hold {savings_balance}, {emergency_fund_months} months. "
            "Up to {fact:isa_allowance} a year can go in an ISA."
        ),
        FACTS,
    )
    sigs = {"savings_balance": 123_456, "emergency_fund_months": Decimal("1.5")}
    r = playbook.render(card, sigs, FACTS, "gentle", TODAY)
    assert r is not None
    assert r.body == (
        "You hold £1,234.56, 1.5 months. Up to £20,000 a year can go in an ISA."
    )
    assert r.figures == (
        ("emergency_fund_months", "1.5"),
        ("savings_balance", "£1,234.56"),
    )
    (f,) = r.facts
    assert (f.value, f.checked_on, f.may_be_out_of_date) == (
        "£20,000",
        date(2026, 4, 10),
        False,
    )


def test_fact_checked_before_this_tax_year_may_be_out_of_date():
    old = {
        "isa_allowance": playbook.Fact(
            "isa_allowance",
            "ISA allowance",
            2_000_000,
            "money",
            "https://www.gov.uk/individual-savings-accounts",
            date(2026, 4, 5),
        )
    }
    card = playbook.parse_card(_card("Up to {fact:isa_allowance}."), old)
    sigs = {"emergency_fund_months": Decimal("1")}
    r = playbook.render(card, sigs, old, "blunt", TODAY)
    assert r is not None and r.facts[0].may_be_out_of_date
    # on 5 April the 2025-26 tax year is still current
    r = playbook.render(card, sigs, old, "blunt", date(2026, 4, 5))
    assert r is not None and not r.facts[0].may_be_out_of_date


def test_no_render_when_a_slot_has_no_figure():
    card = playbook.parse_card(_card("You hold {savings_balance}."), FACTS)
    sigs = {"emergency_fund_months": Decimal("1"), "savings_balance": None}
    assert playbook.render(card, sigs, FACTS, "gentle", TODAY) is None


@pytest.mark.parametrize(
    "body",
    ["Hold {made_up_slot}.", "Up to {fact:made_up}.", "A {broken slot}.", "A }."],
)
def test_unknown_slot_or_fact_fails_validation(body: str):
    with pytest.raises(playbook.PlaybookError):
        playbook.parse_card(_card(body), FACTS)


money = st.one_of(st.none(), st.integers(-(10**9), 10**9))
months = st.one_of(
    st.none(), st.decimals(0, 100, places=1, allow_nan=False, allow_infinity=False)
)


@given(savings=money, ef=months, streak=st.one_of(st.none(), st.integers(0, 12)))
def test_no_card_renders_when_its_condition_is_false_and_rendering_is_deterministic(
    savings: int | None, ef: Decimal | None, streak: int | None
):
    book = playbook.load()
    sigs: dict[str, int | Decimal | None] = dict.fromkeys(playbook.SIGNALS)
    sigs |= {
        "savings_balance": savings,
        "emergency_fund_months": ef,
        "subscription_rise_streak": streak,
        "subscription_spend_last_month": savings,
        "subscription_spend_rise": savings,
        "essential_monthly_spend": savings,
    }
    for card in book.cards:
        for tone in ("gentle", "blunt"):
            r = playbook.render(card, sigs, book.facts, tone, TODAY)
            if not playbook.holds(card.when, sigs):
                assert r is None
            assert r == playbook.render(card, sigs, book.facts, tone, TODAY)


def test_repo_cards_load_and_each_card_file_is_named_by_its_id():
    book = playbook.load()
    assert {c.id for c in book.cards} >= {"emergency-fund", "subscription-creep"}
    for path in sorted((playbook.DATA / "cards").glob("*.json")):
        assert json.loads(path.read_text())["id"] == path.stem


def test_a_card_whose_file_name_differs_from_its_id_fails(tmp_path: Path):
    (tmp_path / "cards").mkdir()
    (tmp_path / "facts.json").write_text((playbook.DATA / "facts.json").read_text())
    (tmp_path / "cards" / "other-name.json").write_text(json.dumps(_card("Hi.")))
    with pytest.raises(playbook.PlaybookError):
        playbook.load(tmp_path)


# --- tone, priority and constraints from Memories (#264) --------------------


@dataclass(frozen=True)
class _Mem:
    type: str
    tags: tuple[str, ...]
    priority: int | None = None
    amount_minor: int | None = None


def _M(
    type_: str, tags: list[str], priority: int | None = None, amount: int | None = None
) -> _Mem:
    return _Mem(type_, tuple(tags), priority, amount)


def test_tone_defaults_to_gentle_and_follows_the_preference():
    assert playbook.choose_tone([]) == "gentle"
    assert playbook.choose_tone([_M("preference", ["tone", "blunt"])]) == "blunt"
    assert (
        playbook.choose_tone([_M("preference", ["blunt"])]) == "gentle"
    )  # no tone tag
    assert playbook.choose_tone([_M("goal", ["tone", "blunt"])]) == "gentle"
    both = [_M("preference", ["tone", "blunt"]), _M("preference", ["tone", "gentle"])]
    assert playbook.choose_tone(both) == "gentle"


def test_goal_priority_orders_cards_and_amount_breaks_ties():
    cards = playbook.load().cards
    mems = [
        _M("goal", ["student-money"], 2),
        _M("goal", ["emergency-fund"], 1),
        _M("goal", ["credit-cards"], 2, 900_000),
    ]
    got = [c.id for c in playbook.prioritise(cards, mems)][:3]
    assert got == ["emergency-fund", "credit-cards", "student-money"]
    assert playbook.prioritise(cards, mems) == playbook.prioritise(cards, mems)


def test_constraint_suppresses_a_card_by_its_words():
    cards = playbook.load().cards
    out = playbook.prioritise(cards, [_M("constraint", ["small-daily-spends"])])
    assert "small-daily-spends" not in {c.id for c in out}
    assert len(out) == len(cards) - 1
    # a goal never suppresses
    assert len(
        playbook.prioritise(cards, [_M("goal", ["small-daily-spends"], 1)])
    ) == len(cards)
