"""Subscription waste flags and free-trial detection (#106). Synthetic data only."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from random import Random

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.categorise.subscriptions import (
    Charge,
    Flag,
    SubscriptionCost,
    subscription_costs,
)
from finance_agent.ledger import SubscriptionEntry
from test_subscription_costs import GROUPS, TODAY, entry


def flagged(
    entries: list[SubscriptionEntry], charges: list[Charge], today: date = TODAY
) -> dict[int, SubscriptionCost]:
    return {
        c.entry.merchant_id: c
        for c in subscription_costs(entries, charges, today).items
    }


def codes(c: SubscriptionCost) -> set[str]:
    return {f.code for f in c.flags}


def monthly(mid: int, minor: int, n: int = 3, last: date = date(2026, 6, 1),
            account: int = 1) -> list[Charge]:  # fmt: skip
    return [
        Charge(mid, last - timedelta(days=30 * i), minor, account_id=account)
        for i in range(n)
    ]


def test_two_active_subscriptions_in_one_group_overlap() -> None:
    by = flagged(
        [
            entry(1),
            entry(2),
            entry(3, group="Software"),
            entry(4, status="user_cancelled"),
        ],
        monthly(1, 1099) + monthly(2, 799) + monthly(3, 999) + monthly(4, 599),
    )
    assert "overlap" in codes(by[1]) and "overlap" in codes(by[2])
    assert "overlap" not in codes(by[3]) | codes(by[4])  # alone; cancelled


def test_the_same_service_charged_from_two_accounts_is_a_duplicate() -> None:
    by = flagged(
        [entry(1), entry(2, group="Software")],
        monthly(1, 1099, account=1)
        + monthly(1, 1099, last=date(2026, 6, 12), account=2)
        # a card switched last month is not a duplicate
        + monthly(2, 999, n=2, account=1, last=date(2026, 5, 2))
        + [Charge(2, date(2026, 6, 1), 999, account_id=2)],
    )
    assert "duplicate" in codes(by[1])
    assert "duplicate" not in codes(by[2])


def test_a_charge_after_the_user_cancelled_is_flagged() -> None:
    # entry() is changed (cancelled) on 2026-01-01
    by = flagged(
        [entry(1, status="user_cancelled", source="user"),
         entry(2, status="user_cancelled", source="user", group="Software")],
        [Charge(1, date(2025, 12, 1), 1099), Charge(1, date(2026, 1, 1), 1099),
         Charge(1, date(2026, 2, 1), 1099), Charge(1, date(2026, 3, 1), 1099),
         Charge(2, date(2025, 12, 1), 999)],
    )  # fmt: skip
    assert by[1].flags == (Flag("charged_after_cancel", on=date(2026, 2, 1)),)
    assert by[1].status == "cancelled" and not by[1].counted
    assert by[2].flags == ()


def test_a_price_rise_compares_the_latest_price_with_the_previous_one() -> None:
    by = flagged(
        [entry(1), entry(2, kind="bill", group="Energy"), entry(3, group="Software")],
        monthly(1, 1099, last=date(2026, 3, 3))
        + monthly(1, 1299, n=2, last=date(2026, 5, 2))
        + monthly(2, 8000, last=date(2026, 5, 4)) + [Charge(2, date(2026, 6, 3), 9500)]
        # jitter within 5% and a cut are not rises
        + [Charge(3, date(2026, 4, 1), 1000), Charge(3, date(2026, 5, 1), 1040),
           Charge(3, date(2026, 6, 1), 900)],
    )  # fmt: skip
    rise = [f for f in by[1].flags if f.code == "price_rise"]
    assert rise == [Flag("price_rise", date(2026, 4, 2), 1099, 1299)]
    assert Flag("price_rise", date(2026, 6, 3), 8000, 9500) in by[2].flags  # Bills too
    assert by[3].flags == ()


def test_an_old_price_rise_is_no_longer_flagged() -> None:
    by = flagged(
        [entry(1)],
        [Charge(1, date(2025, 4, 1), 999)]
        + monthly(1, 1199, n=14, last=date(2026, 6, 1)),
    )
    assert by[1].flags == ()


def named(mid: int, merchant: str) -> SubscriptionEntry:
    return replace(entry(mid), merchant=merchant)


def test_a_small_first_charge_from_a_known_service_starts_a_trial() -> None:
    by = flagged([named(1, "SPOTIFY")], [Charge(1, date(2026, 6, 1), 99)])
    trial = by[1].trial
    assert trial is not None and not trial.converted
    assert (trial.started_on, trial.converts_on) == (date(2026, 6, 1), date(2026, 7, 1))
    assert by[1].flags == ()


def test_a_trial_that_converted_is_flagged() -> None:
    by = flagged(
        [named(1, "SPOTIFY")],
        [Charge(1, date(2026, 5, 1), 99), Charge(1, date(2026, 6, 1), 1199)],
    )
    assert by[1].trial is not None and by[1].trial.converted
    # the step from the trial price is not also a price rise
    assert by[1].flags == (Flag("trial_converted", on=date(2026, 6, 1)),)


def test_no_trial_for_an_unknown_service_or_a_full_first_charge() -> None:
    by = flagged(
        [entry(1), named(2, "SPOTIFY")],
        [Charge(1, date(2026, 6, 1), 99), Charge(2, date(2026, 6, 1), 1199)],
    )
    assert by[1].trial is None and by[2].trial is None


# --- properties ---------------------------------------------------------------

NAMES = ("SYNTH A", "SYNTH B", "SPOTIFY", "Spotify", "NETFLIX.COM")
Rows = tuple[list[SubscriptionEntry], list[Charge]]
AMOUNTS = st.sampled_from((99, 999, 1099, 1299)) | st.integers(1, 10**5)


@st.composite
def registry_and_charges(draw: st.DrawFn) -> Rows:
    es: list[SubscriptionEntry] = []
    cs: list[Charge] = []
    for mid in range(1, draw(st.integers(0, 6)) + 1):
        e = entry(
            mid,
            *draw(st.sampled_from(GROUPS)),
            draw(st.sampled_from(("active", "suggested", "user_cancelled"))),
            draw(st.sampled_from(("weekly", "monthly", "annual", None))),
        )
        es.append(replace(e, merchant=draw(st.sampled_from(NAMES))))
        for _ in range(draw(st.integers(0, 6))):
            on = draw(st.dates(date(2025, 1, 1), TODAY))
            cur = draw(st.sampled_from(("GBP", "GBP", "EUR")))
            cs.append(Charge(mid, on, draw(AMOUNTS), cur, draw(st.integers(1, 3))))
    return es, cs


@given(registry_and_charges())
def test_no_flag_fires_on_a_lapsed_or_cancelled_item(rows: Rows) -> None:
    for c in subscription_costs(*rows, TODAY).items:
        if c.status in ("lapsed", "cancelled"):
            assert codes(c) <= {"charged_after_cancel"}
        if "charged_after_cancel" in codes(c):
            assert c.status == "cancelled"


@given(registry_and_charges(), st.randoms(use_true_random=False))
def test_flags_do_not_depend_on_row_order(rows: Rows, rnd: Random) -> None:
    es, cs = list(rows[0]), list(rows[1])
    before = subscription_costs(es, cs, TODAY)
    rnd.shuffle(es)
    rnd.shuffle(cs)
    after = subscription_costs(es, cs, TODAY)
    assert [(c.flags, c.trial) for c in after.items] == [
        (c.flags, c.trial) for c in before.items
    ]


# --- verifier #190 ------------------------------------------------------------


def test_the_catch_all_group_is_no_overlap() -> None:
    # Xbox Game Pass and Patreon share "Other subscriptions" but replace nothing
    by = flagged(
        [entry(1, group="Other subscriptions"), entry(2, group="Other subscriptions")],
        monthly(1, 999) + monthly(2, 500),
    )
    assert by[1].flags == () and by[2].flags == ()


def test_a_cheap_one_off_between_two_same_prices_is_no_rise() -> None:
    by = flagged(
        [entry(1)],
        monthly(1, 1099, n=5, last=date(2026, 4, 1))
        + [Charge(1, date(2026, 4, 20), 199), Charge(1, date(2026, 5, 1), 1099),
           Charge(1, date(2026, 6, 1), 1099)],
    )  # fmt: skip
    assert by[1].flags == ()


def test_two_trial_charges_then_the_full_price_is_no_rise() -> None:
    by = flagged(
        [named(1, "SPOTIFY")],
        [Charge(1, date(2026, 4, 1), 99), Charge(1, date(2026, 5, 1), 99),
         Charge(1, date(2026, 6, 1), 1199)],
    )  # fmt: skip
    assert by[1].flags == (Flag("trial_converted", on=date(2026, 6, 1)),)


def test_a_regroup_after_cancel_keeps_the_cancel_date() -> None:
    # cancelled on 2026-01-01 (T0), regrouped on 2026-03-01: the February
    # charge still came after the cancel
    e = replace(
        entry(1, status="user_cancelled", source="user"),
        changed_at=datetime(2026, 3, 1, tzinfo=UTC),
        status_since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    by = flagged([e], [Charge(1, date(2026, 2, 1), 1099)])
    assert by[1].flags == (Flag("charged_after_cancel", on=date(2026, 2, 1)),)


@given(registry_and_charges(), st.integers(1, 10**4), st.integers(0, 36))
def test_a_flat_price_with_any_one_off_never_rises(
    rows: Rows, price: int, at: int
) -> None:
    # adversarial: a steady price with one stray charge anywhere after its
    # first charge, ending on that price, never reads as a rise
    cs = monthly(99, price, n=6)
    stray = Charge(99, date(2026, 6, 1) - timedelta(days=at * 4 + 1), price // 3 + 1)
    by = flagged([entry(99)], [*cs, stray])
    assert "price_rise" not in codes(by[99])
