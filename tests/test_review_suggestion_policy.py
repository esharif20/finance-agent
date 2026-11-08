"""The Review Queue's suggester after the 461-as-Groceries bug: payee-type and
lexicon rules, a zero-shot tier, a floored and diversity-checked kNN over
confident labels, then the stored LLM answer. Synthetic Merchants and the fake
embedder only; no Ollama."""

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine

import categoriser
import review_suggestions as rs
from finance_agent import categorise
from finance_agent.categorise import auto, embedding, payees, zero_shot
from finance_agent.categorise.embedding import TrigramEmbedder
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from test_categorise_embedding import _import  # pyright: ignore[reportPrivateUsage]

GROCERIES = ("Food & Drink", "Groceries")
GROCERS = [
    f"ZZQ PANTRY {w}"
    for w in "ALPHA BRAVO CHARLIE DELTA ECHO FOXTROT GOLF HOTEL INDIA JULIET".split()
]
TARGETS = [
    "J QWERTON",
    "MR A ZELLBY",
    "FLYWIRE PAYMENTS",
    "MISTRAL AI",
    "QQX UNRELATED",
    "ZZQ PANTRY KILO",
]


@pytest.fixture
def conn(engine: Engine, tmp_path: Path) -> Iterator[Connection]:
    with engine.begin() as c:
        _import(c, tmp_path, 1, [(n, -500) for n in GROCERS + TARGETS])
        led = Ledger(c, 1)
        ids = {m.name: m.id for m in led.merchants()}
        for n in GROCERS:  # the ten Corrections left after the revert
            led.correct_merchant(ids[n], GROCERIES)
        assert embedding.refresh(c, 1, TrigramEmbedder()) is not None
        yield c


def _suggestions(
    conn: Connection, suggester: Callable[..., dict[str, list[tuple[str, str]]]]
) -> dict[str, tuple[tuple[str, str], ...]]:
    q = ReviewQueue(conn, 1, suggester=lambda t, lab: suggester(conn, 1, t, lab))
    return {i.title.upper(): i.suggestions for i in q.items() if i.type == "category"}


def test_the_old_suggester_reproduces_the_bug(conn: Connection) -> None:
    got = _suggestions(conn, embedding.suggest)
    assert all(v[0] == GROCERIES for v in got.values() if v)


def test_ten_groceries_labels_never_make_everything_groceries(conn: Connection) -> None:
    got = _suggestions(conn, auto.suggest)
    assert got["J QWERTON"] == () and got["MR A ZELLBY"] == ()  # a person: none
    assert got["FLYWIRE PAYMENTS"][0] == ("Bills", "Other bills")
    assert got["MISTRAL AI"][0] == ("Subscriptions", "AI tools")
    # one label Category: the kNN is not diverse enough to suggest alone
    assert got["QQX UNRELATED"] == () and got["ZZQ PANTRY KILO"] == ()


def test_weak_knn_falls_back_to_the_stored_llm_guess() -> None:
    hits = [(400, GROCERIES)]
    takeaway = ("Food & Drink", "Takeaway")
    assert auto.choose("QQX THING", hits, 611, None, takeaway) == [takeaway]
    assert auto.choose("QQX THING", hits, None) == []
    assert auto.choose("QQX THING", [(700, GROCERIES)], 611, None, takeaway) == [
        GROCERIES,
        takeaway,
    ]
    assert auto.choose("SARAH VOLKANE", [(999, GROCERIES)], 611, None, takeaway) == []


def test_diversity_needs_several_categories_none_dominant() -> None:
    assert not auto.diverse({i: {GROCERIES} for i in range(10)})
    cats = [("A", str(i)) for i in range(5)]
    assert auto.diverse({i: {cats[i % 5]} for i in range(10)})
    assert not auto.diverse(
        {
            0: {cats[0]},
            1: {cats[0]},
            2: {cats[0]},
            3: {cats[1]},
            4: {cats[2]},
            5: {cats[3]},
            6: {cats[4]},
        }
        | {7: {cats[0]}, 8: {cats[0]}}  # 5 of 9: over half
    )


@pytest.mark.parametrize(
    "name",
    [
        "J QWERTON",
        "MR A ZELLBY",
        "SMITHSON K",
        "FPO LEO MARTWICK",
        "SARAH VOLKANE",
        "STANDING ORDER R PELLINGHAM",
    ],
)
def test_person_names(name: str) -> None:
    assert payees.person(name)


@pytest.mark.parametrize(
    "name",
    [
        "TOWN MARKET",
        "JACKS FOOD AND WINE",
        "S & J FAST FOOD",
        "B Q",
        "ZTL KESTREL MOBILE",
        "FIREPITT",
        "CANVA",
        "AMAZON.CO.UK",
        "SQ COFFEE HOUSE",
        "MAX 123",
        "",
    ],
)
def test_businesses_are_not_people(name: str) -> None:
    assert not payees.person(name)


@given(st.text(max_size=80))
def test_person_and_rule_never_raise(name: str) -> None:
    payees.person(name)
    payees.rule(name)


def test_the_lexicon_covers_the_plainly_named() -> None:
    assert payees.rule("JACKS FOOD AND WINE") == GROCERIES
    assert payees.rule("TOWN MARKET") == GROCERIES
    assert payees.rule("S & J FAST FOOD") == ("Food & Drink", "Takeaway")
    assert payees.rule("CANVA") == ("Subscriptions", "Software")
    assert payees.rule("DWP DEBT MANAGEMENT") == ("Bills", "Other bills")
    assert payees.rule("ZZQ NOTHING") is None


def test_every_rule_and_description_names_a_current_category() -> None:
    leaves = {(t, s) for t, subs in categorise.TAXONOMY.items() for s in subs}
    assert {c for _, c in payees.RULES} <= leaves
    assert set(zero_shot.DESCRIPTIONS) == leaves - {categorise.UNCATEGORISED}


def test_zero_shot_needs_cached_labels_and_a_calibrated_margin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    e = TrigramEmbedder()
    [v] = e.embed(["SUPERMARKET GROCER"])
    model = "test-trigrams"
    assert zero_shot.guess(model, v) is None  # nothing cached: no Ollama call
    zero_shot.prime(model, e)
    assert zero_shot.guess(model, v) is None  # not calibrated
    monkeypatch.setitem(zero_shot.MARGINS, model, 0)
    assert zero_shot.guess(model, v) == GROCERIES


def test_floors_and_margins_match_the_committed_evals() -> None:
    base = categoriser.read_results()
    ollama = categoriser.read_results(categoriser.RESULTS_OLLAMA)
    assert (
        auto.FLOORS["hashed-trigrams-v1"]
        == base["models"]["nn"]["calibration"]["threshold"]
    )
    assert (
        auto.FLOORS["nomic-embed-text"]
        == ollama["models"]["nn"]["calibration"]["threshold"]
    )
    got = json.loads(rs.RESULTS.read_text())
    assert (
        zero_shot.MARGINS.get("hashed-trigrams-v1")
        == got["calibration"]["zero_shot_margin"]
    )


def test_the_eval_gate() -> None:
    m = {
        k: v["metrics"] for k, v in json.loads(rs.RESULTS.read_text())["models"].items()
    }
    assert m["old"]["false_groceries"] > 0.5  # the bug, reproduced
    for k in ("new", "new_few_labels", "new_nomic"):
        assert m[k]["false_groceries"] <= 0.01
        assert m[k]["person_suggested"] == 0
        assert m[k]["accuracy"] >= 0.7
    assert m["lexicon"]["accuracy"] >= 0.85
