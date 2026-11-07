# synthetic
"""Engine detectors (#148): typed Insight candidates with impact in pence, a
confidence and evidence ids; pure, order-free, and Insufficient below the
ADR 0015 gates. Synthetic data only."""

import random
from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.engine import detectors as det
from finance_agent.engine.baselines import Insufficient

STREAM = ("Subscriptions", "Streaming")
SHOP = ("Shopping", "Online")
TAKE = ("Food & Drink", "Takeaway")


def _monthly(merchant: str, pences: list[int]) -> list[det.Row]:
    return [
        det.Row(i + 1, date(2024 + (i // 12), i % 12 + 1, 5), merchant, STREAM, -p)
        for i, p in enumerate(pences)
    ]


def _found(result: list[det.Insight] | Insufficient) -> list[det.Insight]:
    assert not isinstance(result, Insufficient)
    return result


# --- price increases ------------------------------------------------------------


def test_a_step_up_after_a_monthly_series_is_a_price_increase() -> None:
    rows = _monthly("STREAMLY", [1_099] * 4 + [1_299] * 2)
    found = det.price_increases(rows)
    assert found == [
        det.Insight(
            "price_increase",
            "STREAMLY",
            date(2024, 5, 5),
            2_400,
            det.CONF_PRICE,
            (4, 5),
        )
    ]


def test_no_price_increase_without_a_series_before_it() -> None:
    assert isinstance(
        det.price_increases(_monthly("STREAMLY", [1_099, 1_299])), Insufficient
    )
    # a series, but the dearer charge is a drop or within the 5% band
    assert det.price_increases(_monthly("STREAMLY", [1_099] * 4 + [999, 1_120])) == []


def test_a_dearer_charge_off_the_cadence_is_not_a_price_increase() -> None:
    rows = _monthly("STREAMLY", [1_099] * 4)
    rows.append(det.Row(9, date(2024, 4, 20), "STREAMLY", STREAM, -1_299))
    assert det.price_increases(rows) == []


# --- Category spikes ------------------------------------------------------------


def _takeaway(extra: list[tuple[int, date, int]]) -> list[det.Row]:
    rows = [
        det.Row(m, date(2024, m, 10), "WOKWAY KITCHEN", TAKE, -1_000)
        for m in range(1, 8)
    ]
    return rows + [det.Row(i, d, "WOKWAY KITCHEN", TAKE, a) for i, d, a in extra]


def test_a_spike_is_flagged_on_the_day_it_crosses_with_the_excess_so_far() -> None:
    rows = _takeaway([(8, date(2024, 7, 20), -600), (9, date(2024, 7, 25), -600)])
    [spike] = _found(det.category_spikes(rows))
    assert spike == det.Insight(
        "category_spike",
        "Food & Drink / Takeaway",
        date(2024, 7, 20),
        600,
        det.CONF_SPIKE_EARLY,  # seven months: no same month a year back
        (7, 8),
    )


def test_a_refund_in_the_month_nets_off_before_the_spike_rule() -> None:
    # verifier note: 1,000 + 600 would cross 1.5 x 1,000, but a 300 Refund the
    # same month leaves 1,300 net, under the line
    rows = _takeaway([(8, date(2024, 7, 12), 300), (9, date(2024, 7, 20), -600)])
    assert det.category_spikes(rows) == []
    # without the Refund the same month is a spike
    assert det.category_spikes([r for r in rows if r.id != 8]) != []


def test_spikes_are_insufficient_below_six_months() -> None:
    rows = [r for r in _takeaway([(8, date(2024, 7, 20), -900)]) if r.id != 1]
    assert det.category_spikes(rows) == Insufficient(5, 6)


# --- upcoming renewals ----------------------------------------------------------


def test_an_annual_subscription_seen_once_renews_a_year_on() -> None:
    rows = [
        det.Row(
            1,
            date(2024, 3, 9),
            "CLOUDNOTE ANNUAL",
            ("Subscriptions", "Software"),
            -4_999,
        )
    ]
    assert det.upcoming_renewals(rows) == [
        det.Insight(
            "upcoming_renewal",
            "CLOUDNOTE ANNUAL",
            date(2025, 2, 9),
            4_999,
            det.CONF_RENEWAL_ONCE,
            (1,),
        )
    ]


def test_a_detected_annual_series_renews_in_any_category() -> None:
    rows = [
        det.Row(
            i,
            date(2021 + i, 6, 1),
            "HALDEN PARKING PERMIT",
            ("Transport", "Parking"),
            -9_000,
        )
        for i in range(3)
    ]
    found = det.upcoming_renewals(rows)
    assert [(i.on, i.confidence, i.evidence) for i in found] == [
        (
            date(2024, 6, 1) - timedelta(days=det.LEAD_DAYS),
            det.CONF_RENEWAL_SERIES,
            (2,),
        )
    ]


def test_a_one_off_purchase_or_a_monthly_charge_is_not_a_renewal() -> None:
    once = [det.Row(1, date(2024, 3, 9), "VOLTSPARK ELECTRONICS", SHOP, -30_000)]
    assert det.upcoming_renewals(once) == []
    # a monthly subscription: each charge is followed by the next long before
    assert det.upcoming_renewals(_monthly("STREAMLY", [1_099] * 14)) == []


# --- recurring payments ---------------------------------------------------------


def test_a_recurring_payment_cites_its_run_and_annualises_it() -> None:
    rows = _monthly("STREAMLY", [999, 1_099, 1_099, 1_099])
    assert det.recurring_payments(rows) == [
        det.Insight(
            "recurring",
            "STREAMLY",
            date(2024, 4, 5),
            13_188,
            det.CONF_RECURRING,
            (2, 3, 4),
        )
    ]
    assert isinstance(det.recurring_payments(rows[:2]), Insufficient)


# --- properties -----------------------------------------------------------------

MERCHANTS = ("STREAMLY", "WOKWAY KITCHEN", "CLOUDNOTE ANNUAL")
CATS = (STREAM, TAKE, ("Subscriptions", "Software"))


@st.composite
def ledgers(draw: st.DrawFn) -> list[det.Row]:
    n = draw(st.integers(0, 60))
    return [
        det.Row(
            i + 1,
            date(2023, 1, 1) + timedelta(days=draw(st.integers(0, 900))),
            draw(st.sampled_from(MERCHANTS)),
            draw(st.sampled_from(CATS)),
            draw(st.sampled_from([-1_099, -1_299, -4_999, -900, 300])),
        )
        for i in range(n)
    ]


@settings(max_examples=60, deadline=None)
@given(ledgers(), st.randoms(use_true_random=False))
def test_detectors_are_order_free_and_cite_only_their_rows(
    rows: list[det.Row], rnd: random.Random
) -> None:
    found = det.detect(rows)
    shuffled = rows[:]
    rnd.shuffle(shuffled)
    assert det.detect(shuffled) == found
    ids = {r.id for r in rows}
    for i in found:
        assert type(i.impact) is int and i.impact > 0
        assert 0 < i.confidence <= 100
        assert i.evidence and set(i.evidence) <= ids


@settings(max_examples=40, deadline=None)
@given(st.integers(500, 5_000), st.integers(106, 200), st.integers(3, 10))
def test_any_monthly_step_up_is_found_with_its_annual_cost(
    price: int, pct: int, before: int
) -> None:
    new = price * pct // 100
    rows = _monthly("STREAMLY", [price] * before + [new])
    [rise] = _found(det.price_increases(rows))
    assert rise.impact == (new - price) * 12
    assert rise.evidence == (before, before + 1)


def test_empty_history_never_crashes() -> None:
    assert det.detect([]) == []
    assert det.upcoming_renewals([]) == []
    for f in (det.price_increases, det.category_spikes, det.recurring_payments):
        assert isinstance(f([]), Insufficient)


# --- verifier #188: adversarial ---------------------------------------------------


@settings(max_examples=60, deadline=None)
@given(ledgers())
def test_a_stopped_merchant_never_gets_a_renewal_or_a_price_rise(
    rows: list[det.Row],
) -> None:
    """Cancelled (`Ledger.cancel_subscription`) or marked not a subscription:
    the user's word beats the rows, in every detector that predicts a charge."""
    stopped = frozenset(MERCHANTS[::2])
    rises = det.price_increases(rows, stopped)
    for i in [*det.upcoming_renewals(rows, stopped), *_or_none(rises)]:
        assert i.key not in stopped
    for i in det.detect(rows, stopped):
        if i.type in ("upcoming_renewal", "price_increase"):
            assert i.key not in stopped
    # the rows still count everywhere else
    assert [i for i in det.detect(rows) if i.type == "category_spike"] == [
        i for i in det.detect(rows, stopped) if i.type == "category_spike"
    ]


def _or_none(result: list[det.Insight] | Insufficient) -> list[det.Insight]:
    return [] if isinstance(result, Insufficient) else result


@settings(max_examples=60, deadline=None)
@given(
    st.integers(500, 5_000),
    st.integers(3, 8),
    st.lists(st.tuples(st.integers(0, 300), st.integers(1, 20_000)), max_size=6),
)
def test_a_refund_never_looks_like_a_price_rise(
    price: int, n: int, refunds: list[tuple[int, int]]
) -> None:
    rows = _monthly("STREAMLY", [price] * n)
    rows += [
        det.Row(100 + k, date(2024, 1, 5) + timedelta(days=d), "STREAMLY", STREAM, a)
        for k, (d, a) in enumerate(refunds)
    ]
    assert det.price_increases(rows) == []


def test_a_weekly_rise_is_annualised_over_52_weeks() -> None:
    rows = [
        det.Row(i + 1, date(2024, 1, 1) + timedelta(weeks=i), "GYMLY", SHOP, -500)
        for i in range(4)
    ]
    rows.append(det.Row(5, date(2024, 1, 29), "GYMLY", SHOP, -600))
    [rise] = _found(det.price_increases(rows))
    assert rise.impact == 100 * 52


def test_a_one_off_buy_on_a_charge_day_is_not_a_price_rise() -> None:
    rows = _monthly("STREAMLY", [1_099] * 4)
    rows.append(det.Row(9, date(2024, 4, 5), "STREAMLY", STREAM, -4_000))
    assert det.price_increases(rows) == []


def test_a_lapsed_annual_series_renews_once_and_never_after_it_is_due() -> None:
    rows = [
        det.Row(i + 1, date(2021 + i, 6, 1), "VAULTBOX", SHOP, -6_000) for i in range(3)
    ]
    renewals = det.upcoming_renewals(rows)  # nothing after 2023: it lapsed
    # one renewal, from the series' last charge; no later one once it is due
    assert [(i.on, i.evidence) for i in renewals] == [(date(2024, 5, 4), (3,))]
