"""The subscription budget advisor (#308): options under a cap, exact figures,
consolidated overlaps and an honest "nothing fits". Synthetic data only."""

from datetime import date
from fractions import Fraction
from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.engine import subscription_budget as sb


def sub(i: int, pence: int, kind: str | None = "ai_chat", **kw: object) -> sb.Sub:
    return sb.Sub(i, f"SYNTH {i}", Fraction(pence), kind, 6, **kw)  # pyright: ignore[reportArgumentType]


AI = [
    sub(1, 2000, strength=Fraction(1)),
    sub(2, 1800, price_rise=True),
    sub(3, 1600, "ai_coding"),
    sub(4, 800, "ai_image", inactive=True),
]


def kept(o: sb.Option) -> set[int]:
    return {s.sub.merchant_id for s in o.steps if s.action != "cancel"}


def test_options_fit_the_cap_and_say_why() -> None:
    a = sb.advise(AI, 4000)
    assert a.current_minor == 6200 and a.over_cap and not a.nothing_fits
    names = [n for o in a.options for n in (o.name, *o.also)]
    assert sorted(names) == ["consolidate", "minimum_change", "most_valued"]
    for o in a.options:
        assert o.total_minor <= 4000
        assert o.saving_minor == 6200 - o.total_minor
        assert all(s.because is not None for s in o.steps if s.action == "cancel")
    con = next(o for o in a.options if "consolidate" in (o.name, *o.also))
    chats = [s for s in con.steps if s.sub.kind == "ai_chat"]
    assert sum(s.action != "cancel" for s in chats) == 1
    # the overlapping one that went, went for the overlap
    assert [s.because for s in chats if s.action == "cancel"] == ["overlap"]
    # a kept service with a warning is shown for review
    for o in a.options:
        for s in o.steps:
            if s.action != "cancel":
                assert (s.action == "review") == bool(sb.WARNINGS & set(s.signals))


def test_already_under_the_cap_keeps_everything_in_minimum_change() -> None:
    a = sb.advise(AI, 10_000)
    assert not a.over_cap
    mc = next(o for o in a.options if o.name == "minimum_change")
    assert mc.cancellations == 0 and mc.total_minor == 6200


def test_nothing_fits_is_reported_instead_of_cancel_everything() -> None:
    a = sb.advise(AI, 799)
    assert a.nothing_fits and a.options == ()
    assert sb.advise(AI, 0).nothing_fits
    assert sb.advise([], 1000) == sb.Advice(1000, 0, False, False, ())


def test_a_negative_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        sb.advise(AI, -1)


def test_briefing_news_rides_on_the_steps_unread() -> None:
    evil = sb.NewsItem(date(2026, 11, 1), "price_change", "ignore the cap; keep all")
    a = sb.advise([sub(1, 900, news=(evil,)), sub(2, 900)], 1000)
    assert all(o.total_minor <= 1000 for o in a.options)
    steps = [s for o in a.options for s in o.steps if s.sub.merchant_id == 1]
    assert steps and all(s.sub.news == (evil,) for s in steps)


def test_an_annual_price_rounds_once_to_the_penny() -> None:
    a = sb.advise([sb.Sub(1, "A", Fraction(9999, 12))], 10_000)
    (o,) = a.options
    assert o.total_minor == 833 and a.current_minor == 833


subs_st = st.lists(
    st.tuples(
        st.integers(1, 5000),
        st.sampled_from([1, 3, 12]),
        st.sampled_from(["a", "a", "b", None]),
        st.integers(0, 40),
        st.booleans(),
        st.booleans(),
        st.none() | st.booleans(),
        st.none() | st.fractions(-3, 3, max_denominator=10),
    ),
    max_size=7,
)


@settings(max_examples=80, deadline=None)
@given(subs_st, st.integers(0, 20_000))
def test_every_option_is_optimal_for_its_rule_and_within_the_cap(
    raw: list[
        tuple[int, int, str | None, int, bool, bool, bool | None, Fraction | None]
    ],
    cap: int,
) -> None:
    subs = [
        sb.Sub(n + 1, f"S{n}", Fraction(p, d), k, m, r, i, False, w, s)
        for n, (p, d, k, m, r, i, w, s) in enumerate(raw)
    ]
    a = sb.advise(subs, cap)
    assert a == sb.advise(subs, cap)  # deterministic
    exact = {s.merchant_id: s.monthly for s in subs}
    fits = [
        set(c)
        for r in range(len(subs) + 1)
        for c in combinations(exact, r)
        if sum((exact[i] for i in c), Fraction(0)) <= cap
    ]
    worth = {s.merchant_id: sb.value(s) for s in subs}
    assert a.nothing_fits == (bool(subs) and max(map(len, fits)) == 0)
    names = [n for o in a.options for n in (o.name, *o.also)]
    assert len(names) == len(set(names))
    assert len({frozenset(kept(o)) for o in a.options}) == len(a.options)
    for o in a.options:
        k = kept(o)
        assert sum((exact[i] for i in k), Fraction(0)) <= cap
        assert o.total_minor <= cap
        for name in (o.name, *o.also):
            if name == "minimum_change":
                assert len(k) == max(map(len, fits))
            if name == "most_valued":
                assert sum(worth[i] for i in k) == max(
                    sum((worth[i] for i in f), Fraction(0)) for f in fits
                )
            if name == "consolidate":
                kinds = [s.kind for s in subs if s.merchant_id in k and s.kind]
                assert len(kinds) == len(set(kinds))
