"""Pairwise preferences (#255): Bradley-Terry MAP strengths and the picker."""

from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.engine import preferences as pref
from finance_agent.engine.preferences import Answer

ITEMS = ("Groceries", "Eating out", "Streaming", "Gym", "Travel")


def test_zero_answers_returns_the_prior_and_still_picks_a_pair() -> None:
    assert pref.strengths(ITEMS, []) == dict.fromkeys(ITEMS, 0.0)
    prior = {"Gym": 0.4, "Travel": -0.2}
    assert pref.strengths(ITEMS, [], prior)["Gym"] == pytest.approx(0.4)
    assert pref.next_pair(ITEMS, []) == ("Groceries", "Eating out")  # tie: item order


def test_one_answer_moves_only_the_two_items_symmetrically() -> None:
    s = pref.strengths(ITEMS, [Answer("Gym", "Travel")])
    assert s["Gym"] > 0 > s["Travel"]
    assert s["Gym"] == pytest.approx(-s["Travel"])
    assert s["Groceries"] == s["Streaming"] == 0.0
    assert pref.win_probability(s, "Gym", "Travel") > 0.5


def test_one_item_winning_everything_stays_finite() -> None:
    answers = [Answer("Gym", other) for other in ITEMS if other != "Gym"] * 20
    s = pref.strengths(ITEMS, answers)
    assert 0 < s["Gym"] < 10
    assert max(s, key=lambda k: s[k]) == "Gym"


def test_more_wins_mean_more_strength() -> None:
    answers = [Answer("Gym", "Travel")] * 3 + [Answer("Travel", "Streaming")] * 3
    s = pref.strengths(ITEMS, answers)
    assert s["Gym"] > s["Travel"] > s["Streaming"]


def test_bad_input_is_refused() -> None:
    with pytest.raises(ValueError):
        pref.strengths(("a", "a"), [])
    with pytest.raises(ValueError):
        pref.strengths(ITEMS, [Answer("Gym", "Unknown")])
    with pytest.raises(ValueError):
        pref.strengths(ITEMS, [Answer("Gym", "Gym")])
    with pytest.raises(ValueError):
        pref.strengths(ITEMS, [], prior_sd=0)
    with pytest.raises(ValueError):
        pref.strengths(ITEMS, [], {"Gym": float("nan")})


def test_fewer_than_two_items_has_no_pair() -> None:
    assert pref.next_pair([], []) is None
    assert pref.next_pair(["Gym"], []) is None


def test_once_every_pair_is_answered_any_pair_may_repeat() -> None:
    answers = [Answer(a, b) for a, b in combinations(ITEMS, 2)]
    assert pref.next_pair(ITEMS, answers) in set(combinations(ITEMS, 2))


def test_prior_from_spend() -> None:
    prior = pref.prior_from_spend({"a": 100, "b": 10_000, "c": 0})
    assert prior["c"] == 0.0
    assert prior["a"] == pytest.approx(-prior["b"])
    assert prior["b"] > 0
    assert pref.prior_from_spend({}) == {}
    with pytest.raises(ValueError):
        pref.prior_from_spend({"a": -1})


answer_lists = st.lists(
    st.tuples(st.sampled_from(ITEMS), st.sampled_from(ITEMS))
    .filter(lambda p: p[0] != p[1])
    .map(lambda p: Answer(*p)),
    max_size=30,
)


@settings(max_examples=60, deadline=None)
@given(answers=answer_lists, data=st.data())
def test_strengths_do_not_depend_on_answer_order(
    answers: list[Answer], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(answers))
    prior = {"Gym": 0.3}
    assert pref.strengths(ITEMS, answers, prior) == pref.strengths(
        ITEMS, shuffled, prior
    )
    assert pref.next_pair(ITEMS, answers) == pref.next_pair(ITEMS, shuffled)


@settings(max_examples=60, deadline=None)
@given(answers=answer_lists)
def test_the_picker_never_repeats_a_pair_while_one_is_unanswered(
    answers: list[Answer],
) -> None:
    asked = {frozenset((a.winner, a.loser)) for a in answers}
    pair = pref.next_pair(ITEMS, answers)
    assert pair is not None and pair[0] != pair[1]
    if len(asked) < len(list(combinations(ITEMS, 2))):
        assert frozenset(pair) not in asked
    assert pref.next_pair(ITEMS, answers) == pair  # deterministic
