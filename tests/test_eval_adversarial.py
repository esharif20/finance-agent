"""Verifier tests for the categoriser evaluation (#112): the baseline cannot see
test gold labels, unseen Merchants really are unseen, and the metrics keep
their invariants on arbitrary input."""

import random
from dataclasses import replace

from hypothesis import given
from hypothesis import strategies as st

import categoriser
from finance_agent import categorise
from metrics import expected_calibration_error, macro_f1, per_category

LABELS = st.sampled_from([("A", "a"), ("A", "b"), ("B", "c")])


def _merchant(r: categoriser.Row) -> str | None:
    return categorise.classify(r.descriptor, r.amount, "DEB", transfer=False).merchant


def test_unseen_merchants_have_no_correction() -> None:
    rows = categoriser.load()
    corrected = {_merchant(r) for r in rows if r.split == "correction"} - {None}
    unseen = [r for r in rows if r.case == "unseen"]
    assert unseen
    assert [r.descriptor for r in unseen if _merchant(r) in corrected] == []


def test_baseline_never_reads_test_gold_labels() -> None:
    rows = categoriser.load()
    wrong = ("Travel", "Flights")
    scrambled = [
        r if r.split == "correction" else replace(r, category=wrong) for r in rows
    ]
    assert categoriser.baseline(scrambled) == categoriser.baseline(rows)


@given(
    st.lists(st.tuples(LABELS, st.one_of(st.none(), LABELS)), max_size=40), st.randoms()
)
def test_macro_f1_is_bounded_and_order_free(
    pairs: list[tuple[tuple[str, str], tuple[str, str] | None]], rnd: random.Random
) -> None:
    gold, pred = [g for g, _ in pairs], [p for _, p in pairs]
    score = macro_f1(gold, pred)
    assert 0.0 <= score <= 1.0
    rnd.shuffle(pairs)
    assert macro_f1([g for g, _ in pairs], [p for _, p in pairs]) == score
    for p, r in per_category(gold, pred).values():
        assert 0.0 <= p <= 1.0 and 0.0 <= r <= 1.0
    # abstaining on everything scores zero, never undefined
    assert macro_f1(gold, [None] * len(gold)) == 0.0


@given(
    st.lists(
        st.tuples(st.floats(0.0, 1.0, allow_nan=False), st.booleans()), max_size=60
    ),
    st.randoms(),
)
def test_ece_is_bounded_and_order_free(
    rows: list[tuple[float, bool]], rnd: random.Random
) -> None:
    ece = expected_calibration_error([c for c, _ in rows], [ok for _, ok in rows])
    assert -1e-12 <= ece <= 1.0 + 1e-12
    rnd.shuffle(rows)
    shuffled = expected_calibration_error([c for c, _ in rows], [ok for _, ok in rows])
    assert abs(shuffled - ece) < 1e-9
