"""Seeded synthetic histories with planted events (#146): the dataset the
detector, ranking and forecast harnesses score against."""

from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

import synthetic_history as sh
from finance_agent import categorise
from synthetic_history import Event, History

seeds = st.integers(0, 2**32 - 1)
months = st.integers(sh.MIN_MONTHS, 30)


@settings(max_examples=25, deadline=None)
@given(seeds, months)
def test_generator_is_deterministic_per_seed(seed: int, n: int) -> None:
    a = sh.generate(seed, histories=2, months=n)
    assert a == sh.generate(seed, histories=2, months=n)
    assert sh.fingerprint(a) == sh.fingerprint(sh.generate(seed, histories=2, months=n))
    assert sh.fingerprint(a) != sh.fingerprint(
        sh.generate(seed + 1, histories=2, months=n)
    )


def _txn(h: History, txn_id: int) -> sh.Txn:
    (t,) = [t for t in h.txns if t.id == txn_id]
    return t


def _detectable(h: History, e: Event) -> bool:
    """The planted event's evidence is in the Transactions themselves."""
    match e.type:
        case "price_rise":
            old, new = (_txn(h, i) for i in e.evidence)
            return (
                old.merchant == new.merchant == e.key
                and -new.amount > -old.amount
                and old.date < new.date == e.date
                and e.impact == (old.amount - new.amount) * 12
            )
        case "category_spike":
            spent = sh.month_spend(h, e.category, e.month)
            return (
                spent >= sh.SPIKE_DETECTABLE * sh.expected_spend(h, e.category, e.month)
                and e.month >= sh.BASELINE_MONTHS  # ADR 0015 gate
                and e.impact > 0
            )
        case "forgotten_renewal":
            prev, renewal = (_txn(h, i) for i in e.evidence)
            gap = (renewal.date - prev.date).days
            between = [
                t
                for t in h.txns
                if t.merchant == e.key and prev.date < t.date < renewal.date
            ]
            return (
                prev.merchant == renewal.merchant == e.key
                and 360 <= gap <= 370
                and not between
                and renewal.date == e.date
                and e.impact == -renewal.amount
            )
        case "payday_shortfall":
            (expense,) = (_txn(h, i) for i in e.evidence)
            dipped = [t for t in h.txns if t.date >= expense.date and t.balance < 0]
            return (
                expense.balance >= 0  # warnable: still in credit after it
                and min(t.date for t in dipped) == e.date
                and e.impact == -min(t.balance for t in dipped)
            )
        case _:
            return False


@settings(max_examples=25, deadline=None)
@given(seeds, months)
def test_planted_events_are_always_detectable_in_ground_truth(
    seed: int, n: int
) -> None:
    for h in sh.generate(seed, histories=2, months=n):
        assert {e.type for e in h.events} == set(sh.EVENT_TYPES)
        for e in h.events:
            assert _detectable(h, e), e


@settings(max_examples=25, deadline=None)
@given(seeds, months)
def test_only_planted_shortfalls_dip_below_zero(seed: int, n: int) -> None:
    for h in sh.generate(seed, histories=2, months=n):
        shortfalls = [e for e in h.events if e.type == "payday_shortfall"]
        for t in h.txns:
            if t.balance < 0:
                # inside a planted cycle: after its expense, before next payday
                assert any(
                    e.date <= t.date < e.date + timedelta(days=31) for e in shortfalls
                )


@settings(max_examples=10, deadline=None)
@given(seeds)
def test_unspiked_months_stay_near_the_seasonal_expectation(seed: int) -> None:
    (h,) = sh.generate(seed, histories=1, months=sh.MIN_MONTHS)
    spikes = {(e.category, e.month) for e in h.events if e.type == "category_spike"}
    for cat in sh.DISCRETIONARY:
        for m in range(sh.MIN_MONTHS):
            if (cat, m) not in spikes:
                assert sh.month_spend(h, cat, m) <= 1.2 * sh.expected_spend(h, cat, m)


def test_rows_are_ordered_balanced_and_use_todays_taxonomy() -> None:
    leaves = {(t, s) for t, subs in categorise.TAXONOMY.items() for s in subs}
    for h in sh.generate(sh.SEED, histories=2, months=sh.MIN_MONTHS):
        balance = h.opening
        for prev, t in zip(h.txns, h.txns[1:], strict=False):
            assert prev.date <= t.date and prev.id < t.id
        for t in h.txns:
            balance += t.amount
            assert t.balance == balance
            assert isinstance(t.amount, int) and t.category in leaves
        assert {t.kind for t in h.txns} >= {"salary", "transfer", "recurring"}


def test_seasonality_and_payday_cycle_are_planted() -> None:
    hs = sh.generate(sh.SEED, histories=3, months=24)
    for h in hs:
        shopping = ("Shopping", "Online")
        dec = sh.expected_spend(h, shopping, 11)
        assert dec > 1.5 * sh.expected_spend(h, shopping, 5)  # December
        hols = ("Travel", "Holidays")
        assert sh.expected_spend(h, hols, 7) > 2 * sh.expected_spend(
            h, hols, 2
        )  # summer
        # discretionary spending clusters in the week after payday
        disc = [t for t in h.txns if t.kind == "discretionary"]
        after = [
            t
            for t in disc
            if 0 <= (t.date - sh.payday_on_or_before(h, t.date)).days < 7
        ]
        assert len(after) / len(disc) > 0.3  # a uniform spread would give ~0.23
