"""#317: code resolves every reference (the resolver table), follow-ups patch
the last Plan, figure and balance questions take no model call, open and
in-depth ones take exactly one writer call with no tool schemas, a miss says
so with chips instead of dumping a table, and every reply carries its timing.
Fixed `today`, synthetic Ledger only."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine

import assistant_golden as golden
from finance_agent.assistant import FakeChatModel, Reply, fast_path, planner
from finance_agent.assistant import resolve as rs
from finance_agent.assistant.graph import MISSES, Assistant, ask
from finance_agent.ledger import Ledger
from finance_agent.ledger.proposals import Memory
from synthetic_assistant import DECEMBER, ME, ROWS, seed

TODAY = date(2026, 1, 20)  # a Tuesday


@pytest.fixture
def led(engine: Engine, tmp_path: Path) -> Iterator[Ledger]:
    seed(engine, ME, tmp_path, DECEMBER + ROWS)
    with engine.begin() as conn:
        yield Ledger(conn, ME)


def ids(led: Ledger, *names: str) -> tuple[int, ...]:
    return tuple(sorted(m.id for m in led.merchants() if m.name in names))


# --- the resolver table, one row at a time -------------------------------------


@pytest.mark.parametrize(
    ("text", "since", "until", "label"),
    [
        ("last month", date(2025, 12, 1), date(2025, 12, 31), "December 2025"),
        ("this month", date(2026, 1, 1), TODAY, "1 January 2026 - 20 January 2026"),
        ("this week", date(2026, 1, 19), TODAY, "19 January 2026 - 20 January 2026"),
        ("since March", date(2025, 3, 1), TODAY, "1 March 2025 - 20 January 2026"),
        ("this year", date(2026, 1, 1), TODAY, "1 January 2026 - 20 January 2026"),
        ("the tax year", date(2025, 4, 6), TODAY, "6 April 2025 - 20 January 2026"),
        ("the last 3 months", date(2025, 10, 1), date(2025, 12, 31), None),
        ("in December 2025", date(2025, 12, 1), date(2025, 12, 31), "December 2025"),
        ("2025-11", date(2025, 11, 1), date(2025, 11, 30), "November 2025"),
    ],
)
def test_periods_resolve_to_exact_dates(
    text: str, since: date, until: date, label: str | None
) -> None:
    p = rs.period(text, TODAY)
    assert p is not None and (p.since, p.until) == (since, until)
    if label:
        assert p.label == label


def test_no_period_is_none() -> None:
    assert rs.period("how much on coffee", TODAY) is None


@pytest.mark.parametrize(
    ("text", "cats", "label"),
    [
        ("food", ("Food & Drink > Groceries", "Food & Drink > Eating out"), None),
        ("eating out", ("Food & Drink > Eating out",), "Eating out"),
        ("transport", ("Transport",), "Transport"),
        ("petrol", ("Transport > Fuel",), "Fuel"),
        ("food and drink", ("Food & Drink",), "Food & Drink"),
    ],
)
def test_category_words_go_through_the_synonym_map(
    text: str, cats: tuple[str, ...], label: str | None
) -> None:
    got, got_label = rs.categories(text)
    assert got == cats
    assert got_label == (label or "Groceries + Eating out")


def test_a_merchant_name_covers_its_longer_names(led: Ledger) -> None:
    known = [(m.id, m.name) for m in led.merchants()]
    got, label, q = rs.merchants("Tesco", known)
    assert got == ids(led, "Tesco", "Tesco Fuel") and label == "Tesco + Tesco Fuel"
    assert q is None
    assert rs.merchants("uber", known)[0] == ids(led, "Uber")
    assert rs.merchants("shel", known)[0] == ids(led, "Shell")  # fuzzy
    assert rs.merchants("starbucks", known) == ((), "", None)  # unknown


def test_a_fuzzy_word_fitting_two_merchants_asks_once() -> None:
    got, _, q = rs.merchants("sainsbur", [(1, "Sainsbury"), (2, "Sainsburys")])
    assert got == () and q == "Did you mean Sainsbury or Sainsburys?"


def test_accounts_resolve_by_the_users_own_banks(led: Ledger) -> None:
    [acct] = led.accounts()
    assert rs.account("my Lloyds", led)[0] == acct.id
    assert rs.account("my current account", led)[0] == acct.id
    assert rs.account("my credit card", led) == (None, "", None)


def test_big_is_over_the_users_own_top_tenth(led: Ledger) -> None:
    assert rs.big_threshold(led) == 4800  # nearest-rank p90 of 8 payments
    r = rs.resolve("big purchases", TODAY, led)
    assert r.threshold_minor == 4800 and "over £48.00 (your top 10%)" in r.labels


def test_subscriptions_are_the_detected_series(led: Ledger) -> None:
    [pret] = ids(led, "Pret A Manger")
    assert rs.recurring("subscriptions", led) is None  # none detected yet
    led.add_subscription(pret, "subscription", "Memberships")
    assert rs.recurring("my subscriptions", led) == ((pret,), "1 subscription")


def test_salary_is_the_income_series_not_transfers(led: Ledger) -> None:
    got = rs.income("my salary", led)
    assert got is not None
    mids, label = got
    assert len(mids) <= 1 and label.startswith("Salary (")


def _memory(text: str) -> Memory:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Memory(1, "preference", text, (), "t", "q", TODAY, None, now)


def test_rent_and_people_come_from_memories_or_ask_once(led: Ledger) -> None:
    r = rs.resolve("rent", TODAY, led)
    assert r.question and "rent" in r.question and not r.merchant_ids
    r = rs.resolve("rent", TODAY, led, [_memory("My rent goes to Deliveroo")])
    assert r.merchant_ids == ids(led, "Deliveroo") and r.question is None


def test_usual_this_year_and_currency(led: Ledger) -> None:
    r = rs.resolve("usual coffee this year in euros", TODAY, led)
    assert r.compare == "median3" and "vs your 3-month median" in r.labels
    assert r.period is not None and r.period.since == date(2026, 1, 1)
    assert r.currency == "EUR"


# --- the graph -----------------------------------------------------------------


def run(
    engine: Engine, script: list[str], *messages: str
) -> tuple[list[Any], FakeChatModel]:
    model = FakeChatModel([Reply(content=c) for c in script])
    out: list[Any] = []
    thread = None
    with engine.begin() as conn:
        for msg in messages:
            a = ask(Assistant(model, conn, TODAY, prefetch=True), msg, thread)
            thread = a.thread_id
            out.append(a)
    return out, model


@pytest.fixture
def seeded(engine: Engine, tmp_path: Path) -> Engine:
    seed(engine, ME, tmp_path, DECEMBER + ROWS)
    return engine


def test_figure_questions_and_their_follow_ups_take_no_model_call(
    seeded: Engine,
) -> None:
    turns, model = run(
        seeded,
        [],
        "How much did I spend on food in January 2026?",
        "and the month before?",
        "what about Tesco?",
        "by merchant",
        "How much did I spend on fuel last month?",  # the fast path
        "and the month before?",
    )
    assert model.calls == []
    assert [t.path for t in turns] == ["planner"] * 6
    assert turns[0].reply == (
        "You spent £62.49 on Groceries + Eating out in January 2026."
        " There was none in December 2025."
    )
    assert "in December 2025." in turns[1].reply
    assert "£36.00" in turns[2].reply and "Tesco" in turns[2].reply
    assert "Most went to Tesco Fuel (£36.00)" in turns[3].reply
    assert "November 2025" in turns[5].reply
    assert turns[0].resolved[0].startswith("1 January 2026")
    assert all(t.timing["model_calls"] == 0 for t in turns)


def test_a_comparison_lists_transactions_for_each_figure(seeded: Engine) -> None:
    [a], _ = run(seeded, [], "How much did I spend on food in January 2026?")
    [b], _ = run(seeded, [], "How much did I spend at Tesco in January 2026?")
    for t in (a, b):
        for ev in t.counted:
            assert sum(c.amount_minor for c in ev.transactions) == ev.total_minor


def test_an_open_question_is_one_writer_call_with_no_tools(seeded: Engine) -> None:
    [a], model = run(seeded, ["You spent £92.34 this month."], "Where can I save?")
    [(messages, tools)] = model.calls
    assert tools == ()  # no tool schemas on the writer
    assert a.path == "local_model" and a.timing["model_calls"] == 1
    facts = messages[-2].content
    assert "[resolved]" in facts and "[spending_summary]" in facts
    assert messages[0].content.startswith("You are a careful")  # the stable prefix


def test_an_in_depth_question_fetches_one_round_then_writes(seeded: Engine) -> None:
    [a], model = run(seeded, ["Here is your plan."], "Plan my next 3 months")
    assert len(model.calls) == 1 and model.calls[0][1] == ()
    assert a.route == "plan" and "[forecast_cashflow]" in model.calls[0][0][-2].content


def test_an_open_answer_that_fails_its_check_falls_back_to_guidance(
    seeded: Engine,
) -> None:
    from finance_agent.ledger.insights import Found, Insights

    with seeded.begin() as conn:
        Insights(conn, ME).replace(
            [
                Found(
                    "price_increase",
                    "SYNTH STREAM",
                    "Subscriptions",
                    TODAY,
                    300,
                    80,
                    (),
                )
            ]
        )
    bad = "Cut back and save £999.99."
    [a], _ = run(seeded, [bad, bad], "Where can I save?")
    assert a.path == "planner"
    assert a.reply.startswith("Here's what your Guidance and Insights point to:")
    assert "|" not in a.reply and "£999.99" not in a.reply
    assert "£3.00" in a.reply and a.evidence and a.evidence[0].startswith("insight:")


def test_the_fetch_round_runs_its_reads_at_once(
    seeded: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from finance_agent.assistant import graph

    real = getattr(graph, "run")  # noqa: B009  (the module global the graph calls)

    def slow(*a: Any, **k: Any) -> Any:
        time.sleep(0.2)
        return real(*a, **k)

    monkeypatch.setattr(graph, "run", slow)
    [a], model = run(seeded, ["Here is your plan."], "Plan my next 3 months")
    facts = model.calls[0][0][-2].content
    assert "[forecast_cashflow]" in facts and "[list_guidance]" in facts
    assert a.timing["phases"]["fetch"] < 450  # three 200 ms reads, overlapped


def test_overdraft_is_answered_from_the_bank_balance(
    engine: Engine, tmp_path: Path
) -> None:
    seed(engine, ME, tmp_path, [(1, "DEB", "LANDLORD LTD RENT", -150000)])
    turns, model = run(
        engine,
        [],
        "how much overdraft am I in",
        "what's my balance?",
        "am I in the red?",
        "how much is in my savings?",
        "what's my net worth?",
    )
    assert model.calls == []
    over = turns[0].reply
    assert "is £500.00 overdrawn (balance as of" in over
    assert "I don't know your overdraft limit" in over
    assert "-£500.00" in turns[1].reply
    assert "overdrawn" in turns[2].reply
    assert turns[3].reply == "I can't see a savings account in your data."
    assert "-£500.00" in turns[4].reply and "investments" in turns[4].reply
    assert all(t.path == "planner" for t in turns)
    assert any("balance as of" in r for r in turns[0].resolved)


def test_a_miss_offers_chips_and_never_dumps_a_table(seeded: Engine) -> None:
    before = sum(MISSES.values())
    # the model's reply cites a figure no tool gave: ungrounded twice
    [a], _ = run(seeded, ["About £1,000.00."] * 3, "What is my credit score?")
    assert a.path == "miss" and "|" not in a.reply
    assert len(a.chips) == 3
    assert sum(MISSES.values()) == before + 1


def test_a_near_miss_is_left_to_the_model_not_mis_planned(led: Ledger) -> None:
    # "the shop and the fuel": a word no resolver reads, so no Plan
    q = "How much did I spend at Tesco this year, the shop and the fuel?"
    assert planner.figure(q, TODAY, led) is None


def test_no_golden_question_is_taken_from_the_model_by_the_planner(
    led: Ledger,
) -> None:
    for c in golden.CASES:
        assert planner.classify(c.question) is None, c.id
        assert planner.balances_plan(c.question, TODAY) is None, c.id
        took = planner.figure(c.question, golden.TODAY, led)
        assert took is None or fast_path.parse(c.question, golden.TODAY), c.id


def test_fx_conversion_states_its_rate(seeded: Engine) -> None:
    with seeded.begin() as conn:
        Ledger(conn, ME).set_rate("EUR", Decimal("0.85"), date(2026, 1, 10))
    [a], _ = run(seeded, [], "How much did I spend on food in January 2026 in euros?")
    assert a.path == "planner" and "at the rate of 10 January 2026" in a.reply
    assert "€" in a.reply


def test_the_writer_reads_the_profile_right_after_the_system_prefix(
    seeded: Engine,
) -> None:
    import re

    from finance_agent.engine import profile

    with seeded.connect() as conn:
        text, _ = profile.profile_context(conn, ME, TODAY)
    fig = re.search(r"£[\d,]+\.\d\d", text)
    assert text and fig  # the synthetic Ledger has a profile with figures
    [a], model = run(
        seeded, [f"Your profile shows {fig.group()}."], "Where can I save?"
    )
    [(messages, _)] = model.calls
    assert messages[0].content.startswith("You are a careful")
    assert messages[1].role == "system" and messages[1].content == text
    assert a.path == "local_model"  # a profile figure is grounded


def test_every_reply_offers_chips_the_planner_answers_with_no_call(
    seeded: Engine,
) -> None:
    [first], _ = run(seeded, [], "How much did I spend on food in January 2026?")
    assert first.chips == ("And the month before?", "Compared to usual?", "By merchant")
    for chip in first.chips:
        turns, model = run(
            seeded, [], "How much did I spend on food in January 2026?", chip
        )
        assert model.calls == [] and turns[1].path == "planner", chip
    [open_], _ = run(seeded, ["You spent £92.34 this month."], "Where can I save?")
    assert open_.chips and "Where can I save?" not in open_.chips


def test_by_merchant_on_all_spending_names_where_it_went(
    engine: Engine, tmp_path: Path
) -> None:
    seed(engine, ME, tmp_path, DECEMBER + ROWS)
    with engine.begin() as conn:
        first = ask(
            Assistant(FakeChatModel([]), conn, TODAY, prefetch=True),
            "How much did I spend in December 2025?",
        )
        assert "By merchant" in first.chips
        a = ask(
            Assistant(FakeChatModel([]), conn, TODAY, prefetch=True),
            "By merchant",
            first.thread_id,
        )
    assert a.path == "planner", a.reply
    assert "£112.80" in first.reply and "£112.80" in a.reply
    assert "Most went to" in a.reply
    assert "By merchant" not in a.chips
