"""Insight ranking (#150): Beta-Bernoulli relevance, impact x confidence x
relevance, the Memory feasibility filter. Synthetic data only."""

from fractions import Fraction
from typing import NamedTuple

from hypothesis import given
from hypothesis import strategies as st

from finance_agent.engine.ranking import (
    PRIOR,
    Relevance,
    feasible,
    posteriors,
    rank,
    score,
)


class Item(NamedTuple):
    id: int
    type: str
    category: str
    impact: int
    confidence: int


TYPES = st.sampled_from(("recurring", "price_increase", "category_spike"))
CATS = st.sampled_from(("Food & Drink / Groceries", "Bills / Rent or mortgage"))
signals = st.lists(st.tuples(TYPES, CATS, st.booleans()), max_size=30)
relevance = st.builds(Relevance, st.integers(0, 50), st.integers(0, 50))


def test_the_prior_is_beta_one_one() -> None:
    assert PRIOR.mean() == Fraction(1, 2)
    assert posteriors([]) == {}
    assert posteriors([("a", "c", True), ("a", "c", False), ("a", "c", True)]) == {
        ("a", "c"): Relevance(2, 1)
    }
    assert Relevance(2, 1).mean() == Fraction(3, 5)


@given(relevance)
def test_a_like_raises_and_a_dislike_lowers_the_mean(r: Relevance) -> None:
    assert r.update(False).mean() < r.mean() < r.update(True).mean()


@given(signals, st.lists(st.tuples(TYPES, CATS, st.booleans()), max_size=10))
def test_posteriors_are_monotone_in_each_cells_signals(
    before: list[tuple[str, str, bool]], more: list[tuple[str, str, bool]]
) -> None:
    """Adding likes never lowers a cell, adding dislikes never raises one,
    and the order signals arrive in does not matter."""
    old = posteriors(before)
    likes = posteriors(before + [(t, c, True) for t, c, _ in more])
    dislikes = posteriors(before + [(t, c, False) for t, c, _ in more])
    for cell in likes.keys() | old.keys():
        assert likes[cell].mean() >= old.get(cell, PRIOR).mean()
        assert dislikes[cell].mean() <= old.get(cell, PRIOR).mean()
    assert posteriors(list(reversed(before))) == old


items = st.lists(
    st.builds(
        Item,
        st.just(0),
        TYPES,
        CATS,
        st.integers(0, 3),  # tiny ranges, so ties are common
        st.sampled_from((50, 100)),
    ),
    max_size=12,
).map(lambda xs: [x._replace(id=n) for n, x in enumerate(xs)])


@given(items, signals)
def test_ranking_is_a_stable_sort_by_score(
    xs: list[Item], sig: list[tuple[str, str, bool]]
) -> None:
    rel = posteriors(sig)
    out = rank(xs, rel)
    assert sorted(out) == sorted(xs)
    for a, b in zip(out, out[1:], strict=False):
        sa, sb = score(a, rel), score(b, rel)
        assert sa > sb or (sa == sb and a.id < b.id)  # ties keep the given order


@given(items)
def test_with_no_feedback_relevance_is_flat_and_ties_keep_order(xs: list[Item]) -> None:
    same = [x._replace(impact=7, confidence=70) for x in xs]
    assert rank(same, {}) == same
    assert rank(xs, {}) == sorted(xs, key=lambda i: -i.impact * i.confidence)


def test_relevance_reorders_equal_impacts() -> None:
    a = Item(1, "category_spike", "Bills / Rent or mortgage", 1000, 80)
    b = Item(2, "price_increase", "Food & Drink / Groceries", 1000, 80)
    rel = posteriors([("category_spike", "Bills / Rent or mortgage", False)])
    assert rank([a, b], rel) == [b, a]
    assert rank([a, b], {}) == [a, b]


def test_a_constraint_tag_naming_the_subject_filters_it() -> None:
    a = Item(1, "category_spike", "Bills / Rent or mortgage", 1000, 80)
    b = Item(2, "price_increase", "Food & Drink / Groceries", 900, 80)

    def subject(i: Item) -> str:
        return i.category

    assert feasible([a, b], subject, ["rent"]) == [b]
    assert feasible([a, b], subject, ["RENT"]) == [b]
    assert feasible([a, b], subject, ["ren", "mortgages"]) == [a, b]  # whole words
    assert feasible([a, b], subject, []) == [a, b]


def test_a_hyphenated_constraint_tag_matches_its_words() -> None:
    """Verifier (#150): Memory tags are `[a-z][a-z0-9-]*`, so "eating-out" must
    rule out "Eating out" (all its words present), and "out" alone must not
    match "outdoor"."""
    a = Item(1, "category_spike", "Food & Drink / Eating out", 1000, 80)
    b = Item(2, "category_spike", "Leisure / Outdoor", 900, 80)

    def subject(i: Item) -> str:
        return i.category

    assert feasible([a, b], subject, ["eating-out"]) == [b]
    assert feasible([a, b], subject, ["out-eating"]) == [b]  # words, any order
    assert feasible([a, b], subject, ["eating-in"]) == [a, b]
