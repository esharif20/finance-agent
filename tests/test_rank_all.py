"""The batched kNN (`rank_all`) answers exactly as `rank` does per target."""

import random
import time

from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.categorise.embedding import (
    SCALE,
    SUGGESTIONS,
    Vector,
    rank,
    rank_all,
)

Cat = tuple[str, str]
Vecs = dict[int, Vector]
Targets = dict[str, int]
Labelled = dict[int, set[Cat]]
World = tuple[Vecs, Targets, Labelled]

CATS = [
    ("Food", "Groceries"),
    ("Food", "Eating out"),
    ("Bills", "Energy"),
    ("Travel", "Rail"),
]


def per_target(
    vecs: Vecs, targets: Targets, labelled: Labelled
) -> dict[str, list[tuple[int, Cat]]]:  # the old path, one target at a time
    out: dict[str, list[tuple[int, Cat]]] = {}
    for key, m in targets.items():
        if m not in vecs:
            continue
        found = rank(
            vecs[m], [(vecs[n], c) for n, c in labelled.items() if n != m and n in vecs]
        )
        if found:
            out[key] = found[:SUGGESTIONS]
    return out


@st.composite
def worlds(draw: st.DrawFn) -> World:
    dim = draw(st.integers(1, 6))
    n = draw(st.integers(1, 12))
    # quantised vectors (largest component +-SCALE) and, sometimes, toy ones
    big = draw(st.booleans())
    comp = st.integers(-SCALE, SCALE) if big else st.integers(-3, 3)
    vecs: Vecs = {
        i: tuple(draw(st.lists(comp, min_size=dim, max_size=dim))) for i in range(n)
    }
    if big:  # as `quantise` stores them: some component at +-SCALE
        vecs = {
            i: (SCALE, *v[1:]) if v[0] >= 0 else (-SCALE, *v[1:])
            for i, v in vecs.items()
        }
    odd = draw(st.booleans())
    if odd:  # a vector of another length is skipped, never compared
        vecs[n] = (SCALE,) * (dim + 1)
    labelled = {
        i: set(draw(st.lists(st.sampled_from(CATS), min_size=1, max_size=2)))
        for i in draw(st.sets(st.sampled_from(sorted(vecs) + [99])))
    }
    targets = {f"k{i}": i for i in draw(st.sets(st.sampled_from(sorted(vecs) + [98])))}
    return vecs, targets, labelled


@settings(max_examples=300)
@given(worlds())
def test_rank_all_equals_rank_per_target(world: World) -> None:
    vecs, targets, labelled = world
    assert rank_all(vecs, targets, labelled) == per_target(vecs, targets, labelled)


def test_near_ties_between_categories_are_exact() -> None:
    # adversarial: two labelled Merchants a hair apart; the pre-filter must not
    # drop the exact winner nor reorder Categories
    t = (SCALE, 1, 0)
    vecs: Vecs = {
        0: t,
        1: (SCALE, 2, 0),
        2: (SCALE, 1, 1),
        3: (SCALE, 0, 1),
        4: (-SCALE, 5, 5),
    }
    labelled = {1: {CATS[0]}, 2: {CATS[1]}, 3: {CATS[1], CATS[2]}, 4: {CATS[3]}}
    targets = {"a": 0, "b": 3}
    assert rank_all(vecs, targets, labelled) == per_target(vecs, targets, labelled)


def test_a_review_sized_queue_ranks_fast() -> None:
    rng = random.Random(7)  # noqa: S311 - synthetic vectors
    dim = 768
    vecs: Vecs = {
        i: (SCALE, *(rng.randint(-SCALE, SCALE) for _ in range(dim - 1)))
        for i in range(950)
    }
    labelled = {i: {CATS[i % len(CATS)]} for i in range(150, 950)}
    targets = {f"k{i}": i for i in range(150)}
    t0 = time.perf_counter()
    got = rank_all(vecs, targets, labelled)
    took = time.perf_counter() - t0
    assert got["k0"] == per_target(vecs, {"k0": 0}, labelled)["k0"]
    assert took < 3.0  # was about 11 s target by target; generous for loaded CI
