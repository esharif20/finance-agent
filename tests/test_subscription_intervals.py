"""Interval sources for the monthly cost (#210): the registry's interval, then
the detector, then a looser cadence fit on 2+ charges, then the known-services
catalogue. Synthetic data only."""

from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.categorise.subscriptions import Charge, subscription_costs
from finance_agent.ledger import SubscriptionEntry

TODAY = date(2026, 6, 15)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def entry(
    mid: int, interval: str | None = None, merchant: str | None = None
) -> SubscriptionEntry:
    return SubscriptionEntry(
        mid, merchant or f"SYNTH {mid}", "subscription", "Streaming", "active",
        "pattern", "detection", date(2026, 1, 1), None, interval, T0, T0,
    )  # fmt: skip


def one(e: SubscriptionEntry, charges: list[tuple[date, int]]):  # noqa: ANN201
    (c,) = subscription_costs(
        [e], [Charge(e.merchant_id, d, m) for d, m in charges], TODAY
    ).items
    return c


def test_the_registry_interval_comes_first() -> None:
    c = one(entry(1, "annual"), [(date(2026, 5, 1), 1200), (date(2026, 6, 1), 1200)])
    assert (c.interval, c.interval_source, c.monthly) == ("annual", "registry", 100)


def test_the_detector_reads_two_regular_charges() -> None:
    c = one(entry(1), [(date(2026, 5, 1), 400), (date(2026, 6, 1), 400)])
    assert (c.interval, c.interval_source, c.monthly) == ("monthly", "detected", 400)


def test_drifting_bills_get_an_estimated_interval() -> None:
    # amounts beyond ±5% and one late month: the detector says no
    c = one(
        entry(1),
        [(date(2026, 2, 3), 4000), (date(2026, 3, 3), 5200),
         (date(2026, 4, 14), 4700), (date(2026, 5, 3), 6100)],
    )  # fmt: skip
    assert (c.interval, c.interval_source, c.monthly) == ("monthly", "estimated", 6100)
    assert c.cost_note is None and c.counted


def test_the_loose_fit_tolerance_is_a_third_of_the_cadence() -> None:
    def fit(gap: int) -> str | None:
        d = date(2025, 1, 1)
        rows = [(d, 1000), (d + timedelta(gap), 1300)]
        return one(entry(1), rows).interval

    assert [fit(g) for g in (4, 5, 9, 10)] == [None, "weekly", "weekly", None]
    assert [fit(g) for g in (19, 20, 40, 41)] == [None, "monthly", "monthly", None]
    assert [fit(g) for g in (243, 244, 486, 487)] == [None, "annual", "annual", None]


def test_a_single_charge_from_a_known_service_uses_the_catalogue() -> None:
    c = one(entry(1, merchant="NETFLIX.COM"), [(date(2026, 6, 1), 1099)])
    assert (c.interval, c.interval_source, c.monthly) == ("monthly", "catalogue", 1099)
    tv = one(entry(2, merchant="TV LICENCE"), [(date(2026, 6, 1), 17450)])
    assert (tv.interval, tv.monthly) == ("annual", Fraction(17450, 12))


def test_a_single_unknown_charge_says_only_one_charge() -> None:
    c = one(entry(1), [(date(2026, 6, 1), 1000)])
    assert (c.interval, c.interval_source, c.monthly) == (None, None, None)
    assert (c.cost_note, c.charge_count) == ("interval_needed", 1)


def test_irregular_charges_from_an_unknown_merchant_stay_unknown() -> None:
    c = one(entry(1), [(date(2026, 1, 1), 900), (date(2026, 3, 15), 900)])
    assert (c.interval, c.cost_note, c.charge_count) == (None, "interval_needed", 2)


# --- properties ---------------------------------------------------------------

SOURCES = (None, "registry", "detected", "estimated", "catalogue")


@st.composite
def case(draw: st.DrawFn) -> tuple[SubscriptionEntry, list[Charge]]:
    interval = draw(st.sampled_from(("weekly", "monthly", "annual", None)))
    name = draw(st.sampled_from(("SYNTH 1", "NETFLIX.COM", "TV LICENCE")))
    days = draw(st.lists(st.dates(date(2024, 1, 1), TODAY), max_size=6))
    amounts = st.integers(1, 10**7)
    return entry(1, interval, name), [Charge(1, d, draw(amounts)) for d in days]


@given(case())
def test_the_monthly_figure_is_exact_never_a_float(
    c: tuple[SubscriptionEntry, list[Charge]],
) -> None:
    (x,) = subscription_costs([c[0]], c[1], TODAY).items
    assert x.monthly is None or type(x.monthly) is Fraction
    if x.monthly is not None:
        last = max(c[1], key=lambda ch: (ch.posted_on, ch.currency, ch.minor)).minor
        per = {"weekly": Fraction(52, 12), "monthly": 1, "annual": Fraction(1, 12)}
        assert x.interval is not None and x.monthly == last * per[x.interval]


@given(case())
def test_the_source_order_holds(c: tuple[SubscriptionEntry, list[Charge]]) -> None:
    e, charges = c
    (x,) = subscription_costs([e], charges, TODAY).items
    assert x.interval_source in SOURCES
    assert (x.interval is None) == (x.interval_source is None)
    known = e.merchant != "SYNTH 1"
    if e.interval:  # the registry always wins
        assert (x.interval, x.interval_source) == (e.interval, "registry")
    elif len({ch.posted_on for ch in charges}) < 2:  # no cadence to read
        assert x.interval_source == ("catalogue" if known else None)
    elif not known:
        assert x.interval_source in (None, "detected", "estimated")
    else:  # a known service always has an interval
        assert x.interval_source in ("detected", "estimated", "catalogue")
