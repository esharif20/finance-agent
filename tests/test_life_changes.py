"""Confirmed life changes (#270): only a confirmation resets a series'
baselines and forecasts, dismissing is feedback, undo restores, per user.
Synthetic only."""

import json
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine

import baselines as baselines_eval
from finance_agent.engine.baselines import (
    Insufficient,
    SeasonalBaseline,
    seasonal_baseline,
)
from finance_agent.engine.forecast import forecast_category, project
from finance_agent.ledger import Categorisation, Transaction, UnknownTarget
from finance_agent.ledger.life_changes import Detected, LifeChanges, series_of
from finance_agent.ledger.review import (
    Answer,
    InvalidAnswer,
    ReviewQueue,
    StaleItem,
)

CHANGE = Detected("rent", date(2026, 5, 1), 100_000, 115_000, (1, 2, 3))
ITEM = "life_change:rent:2026-05"


def queue(
    conn: Connection, user: int, found: Sequence[Detected] = (CHANGE,)
) -> ReviewQueue:
    return ReviewQueue(conn, user, detector=lambda _txns: list(found))


def life_items(q: ReviewQueue) -> list[str]:
    return [i.id for i in q.items() if i.type == "life_change"]


def months(values: Sequence[int], start: int = 0) -> list[tuple[date, int]]:
    return [
        (date(2024 + (start + i) // 12, (start + i) % 12 + 1, 10), -v)
        for i, v in enumerate(values)
    ]


# --- the eval gate ----------------------------------------------------------


def test_the_reset_beats_no_reset_after_a_shift() -> None:
    models = json.loads(baselines_eval.RESULTS.read_text())["models"]
    ours, theirs = models["candidate"]["metrics"], models["no_reset"]["metrics"]
    for name in ("after_shift.all.wape", "after_shift_forecast.all.wape"):
        assert ours[name] < theirs[name], name


# --- engine -----------------------------------------------------------------


def test_after_a_reset_the_baseline_reads_only_months_since_it() -> None:
    rows = months([100] * 12 + [300] * 3)  # Jan 2024 .. Mar 2025
    before = seasonal_baseline(rows, date(2025, 4, 1))
    after = seasonal_baseline(rows, date(2025, 4, 1), since=date(2025, 1, 1))
    assert isinstance(before, SeasonalBaseline) and before.expected == 100
    assert after == SeasonalBaseline(300, 15, False)


def test_a_reset_with_no_complete_month_since_is_insufficient() -> None:
    rows = months([100] * 12)
    assert seasonal_baseline(rows, date(2025, 1, 1), since=date(2025, 1, 1)) == (
        Insufficient(0, 1)
    )


def test_a_reset_does_not_open_the_adr_0015_gate() -> None:
    rows = months([100] * 3)
    got = seasonal_baseline(rows, date(2024, 4, 1), since=date(2024, 2, 1))
    assert isinstance(got, Insufficient) and got.need == 6


@settings(max_examples=50)
@given(st.lists(st.integers(0, 10**7), min_size=6, max_size=30))
def test_a_reset_at_or_before_the_history_changes_nothing(values: list[int]) -> None:
    rows = months(values)
    target = date(2024 + len(values) // 12, len(values) % 12 + 1, 1)
    plain = seasonal_baseline(rows, target)
    assert seasonal_baseline(rows, target, since=date(2024, 1, 1)) == plain
    assert seasonal_baseline(rows, target, since=date(2020, 1, 1)) == plain


def test_a_reset_forecast_is_the_level_since_the_change() -> None:
    f = forecast_category([300, 310, 290], 2, reset=True)
    assert f.method == "since_change"
    assert [(b.point, b.lower, b.upper) for b in f.months] == [(300, 290, 310)] * 2
    assert forecast_category([300, 310, 290], 2).method == "insufficient"


def _rent(i: int, pence: int) -> Transaction:
    c = Categorisation("Spending", None, ("Bills", "Rent or mortgage"), "default", 2)
    on = date(2024 + i // 12, i % 12 + 1, 1)
    return Transaction(
        on, "SYNTH", Decimal(-pence).scaleb(-2), None, frozenset(), i, categorisation=c
    )


def test_a_confirmed_change_resets_the_series_forecast_only() -> None:
    txns = [_rent(i, 100_000 if i < 12 else 120_000) for i in range(15)]
    as_of = date(2025, 4, 1)
    rent = ("Bills", "Rent or mortgage")
    plain = project(txns, {}, as_of, 1).categories[rent]
    assert plain.method == "seasonal_naive" and plain.months[0].point == 100_000
    reset = project(txns, {}, as_of, 1, {"rent": date(2025, 1, 1)})
    assert reset.categories[rent].method == "since_change"
    assert reset.categories[rent].months[0].point == 120_000
    other = project(txns, {}, as_of, 1, {"groceries": date(2025, 1, 1)})
    assert other.categories[rent] == plain
    future = project(txns, {}, as_of, 1, {"rent": date(2025, 4, 1)})
    assert future.categories[rent] == plain  # not begun: nothing to reset yet


def test_series_follow_categories_across_taxonomy_versions() -> None:
    assert series_of("Spending", ("Housing", "Rent"), 1) == "rent"
    assert series_of("Spending", ("Bills", "Rent or mortgage"), 2) == "rent"
    assert series_of("Refund", ("Bills", "Energy"), 2) == "bills"
    assert series_of("Spending", ("Food & Drink", "Groceries"), 2) == "groceries"
    assert series_of("Income", None, 2) == "income"
    assert series_of("Transfer", ("Bills", "Energy"), 2) is None
    assert series_of("Spending", ("Food & Drink", "Coffee"), 2) is None


# --- the Review Queue -------------------------------------------------------


def test_a_detection_is_a_question_and_only_yes_stores_it(engine: Engine) -> None:
    with engine.begin() as conn:
        q = queue(conn, 1)
        (item,) = [i for i in q.items() if i.id == ITEM]
        assert item.title == "Did something change in May 2026?"
        assert item.choices == ("yes", "no")
        assert LifeChanges(conn, 1).resets() == {}  # detection alone resets nothing
        q.answer(ITEM, item.version, Answer(choice="yes"))
        assert LifeChanges(conn, 1).resets() == {"rent": date(2026, 5, 1)}
        assert life_items(q) == []


def test_dismissing_is_feedback_and_resets_nothing(engine: Engine) -> None:
    with engine.begin() as conn:
        q = queue(conn, 1)
        (item,) = [i for i in q.items() if i.id == ITEM]
        q.answer(ITEM, item.version, Answer(choice="no"))
        assert life_items(q) == []
        assert LifeChanges(conn, 1).resets() == {}
        d = conn.exec_driver_sql(
            "SELECT action, effect_id FROM review_decision WHERE item_type = ?",
            ("life_change",),
        ).all()
        assert [tuple(r) for r in d] == [("answer", None)]


def test_undo_removes_the_confirmation_and_restores_baselines(engine: Engine) -> None:
    with engine.begin() as conn:
        q = queue(conn, 1)
        (item,) = [i for i in q.items() if i.id == ITEM]
        q.answer(ITEM, item.version, Answer(choice="yes"))
        q.undo()
        assert LifeChanges(conn, 1).resets() == {}
        assert conn.exec_driver_sql("SELECT count(*) FROM life_change").scalar() == 0
        assert life_items(q) == [ITEM]


def test_a_confirmation_for_a_month_with_no_detection_is_refused(
    engine: Engine,
) -> None:
    with engine.begin() as conn:
        q = queue(conn, 1)
        version = next(i.version for i in q.items() if i.id == ITEM)
        for forged in ("life_change:rent:2026-06", "life_change:income:2026-05"):
            with pytest.raises(UnknownTarget):
                q.answer(forged, version, Answer(choice="yes"))
        with pytest.raises(UnknownTarget):  # no detector, no item at all
            ReviewQueue(conn, 1).answer(ITEM, version, Answer(choice="yes"))
        with pytest.raises(InvalidAnswer):
            q.answer(ITEM, version, Answer(category=("Bills", "Energy")))
        assert LifeChanges(conn, 1).resets() == {}


def test_a_detection_gone_since_shown_is_not_stored(engine: Engine) -> None:
    with engine.begin() as conn:
        (item,) = [i for i in queue(conn, 1).items() if i.id == ITEM]
        calls = iter([[CHANGE], []])  # listed while answering, gone when stored
        q = ReviewQueue(conn, 1, detector=lambda _t: next(calls))
        with pytest.raises(StaleItem):
            q.answer(ITEM, item.version, Answer(choice="yes"))
        assert LifeChanges(conn, 1).resets() == {}


def test_confirmations_are_per_user(engine: Engine) -> None:
    with engine.begin() as conn:
        q1 = queue(conn, 1)
        (item,) = [i for i in q1.items() if i.id == ITEM]
        q1.answer(ITEM, item.version, Answer(choice="yes"))
        assert LifeChanges(conn, 2).resets() == {}
        assert life_items(queue(conn, 2)) == [ITEM]
        (mine,) = conn.exec_driver_sql("SELECT id FROM life_change").scalars().all()
        assert LifeChanges(conn, 2).remove(mine) is False  # not user 2's to undo
        assert LifeChanges(conn, 2).forget() == 0
        assert LifeChanges(conn, 1).resets() == {"rent": date(2026, 5, 1)}


def test_the_api_detector_reads_no_figures_from_too_little_history() -> None:
    from finance_agent.api.review_queue import detected

    txns: list[Transaction] = []
    assert detected(txns) == []


def test_migration_0030_round_trips(tmp_path: Path) -> None:
    from test_storage import round_trip

    row = (
        "INSERT INTO life_change (user_id, series, month, before_minor,"
        " after_minor, created_at) VALUES (1, 'rent', '2026-05', 1, 2, 'x')"
    )
    round_trip(tmp_path, "0029", "0030", {"life_change"}, (row,)).dispose()
