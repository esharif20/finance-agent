"""Categoriser evaluation (#112, #113): the synthetic dataset, the Corrections +
rules baseline, the embedding nearest-neighbour model and the shared gate."""

from collections import Counter
from pathlib import Path

import pytest

import categoriser
import gate
from categoriser import Row
from finance_agent import categorise
from finance_agent.categorise import embedding


def _leaves() -> set[tuple[str, str]]:
    return {(top, sub) for top, subs in categorise.TAXONOMY.items() for sub in subs}


def test_committed_dataset_is_the_seeded_generator_output() -> None:
    text = categoriser.DATASET.read_text()
    assert text.startswith("# synthetic\n")
    assert categoriser.generate(categoriser.SEED) == text
    assert categoriser.DATASET_VERSION == 3


def test_dataset_covers_every_case_and_resolves_to_current_categories() -> None:
    rows = categoriser.load()
    test = [r for r in rows if r.split == "test"]
    assert {r.case for r in test} >= {"clean", "noise", "lookalike", "refund", "unseen"}
    assert {r.split for r in rows} == {"correction", "test"}
    assert all(r.amount > 0 for r in rows if r.case == "refund")
    gold = {r.category for r in rows}
    # every gold label names a Category of today's taxonomy, and no two collapse
    # into one, so a taxonomy upgrade only renames classes
    assert {categoriser.resolve(c) for c in gold} <= _leaves()
    assert len({categoriser.resolve(c) for c in gold}) == len(gold)
    assert categorise.UNCATEGORISED not in gold


def test_the_corrections_clear_the_embedding_models_data_gate() -> None:
    """ADR 0015: >= 200 Corrections and >= 5 corrected Merchants per Category."""
    rows = categoriser.load()
    corrections = [r for r in rows if r.split == "correction"]
    assert len(corrections) >= 200
    merchants: dict[tuple[str, str], set[str]] = {}
    for r in corrections:
        merchants.setdefault(r.category, set()).add(categorise.normalise(r.descriptor))
    every = {r.category for r in rows}
    assert {
        c: len(merchants.get(c, ())) for c in every if len(merchants.get(c, ())) < 5
    } == {}


def test_a_correction_beats_the_rules_and_covers_refunds() -> None:
    eats = ("Food & Drink", "Takeaway")
    rows = [
        Row(1, "correction", "clean", "UBER EATS", -1200, eats),
        Row(2, "test", "noise", "UBER EATS 0042", -900, eats),
        Row(3, "test", "refund", "UBER EATS REFUND", 900, eats),
        Row(4, "test", "unseen", "SWIFT CABS", -1500, ("Transport", "Taxi")),
        Row(5, "test", "unseen", "NOWHERE LTD", -100, ("Shopping", "Online")),
    ]
    assert categoriser.baseline(rows) == [
        (eats, 1.0),
        (eats, 1.0),
        (("Transport", "Taxi"), 1.0),
        (None, 0.0),
    ]


def test_without_the_correction_the_rule_decides() -> None:
    taxi = ("Transport", "Taxi")
    rows = [Row(1, "test", "lookalike", "UBER EATS", -900, taxi)]
    # the UK base's sub-brand beats the Uber Merchant (#215)
    assert categoriser.baseline(rows) == [(("Food & Drink", "Takeaway"), 1.0)]


def test_nearest_neighbour_fills_only_what_corrections_and_rules_leave() -> None:
    coffee = ("Food & Drink", "Coffee")
    rows = [
        Row(1, "correction", "clean", "BEAN THERE COFFEE", -300, coffee),
        Row(
            2,
            "correction",
            "clean",
            "HARGREAVE CLOTHING",
            -3000,
            ("Shopping", "Clothing"),
        ),
        Row(3, "test", "unseen", "BEAN HERE BREWS", -300, coffee),  # no rule hits
        Row(4, "test", "unseen", "SWIFT CABS", -1500, ("Transport", "Taxi")),
    ]
    preds = categoriser.nearest_neighbour(rows, embedding.TrigramEmbedder())
    assert preds[0][0] == coffee and 0.5 < preds[0][1] < 1.0
    assert preds[1] == (("Transport", "Taxi"), 1.0)  # the rule, untouched


def test_nearest_neighbour_never_reads_test_gold_labels() -> None:
    from dataclasses import replace

    rows = categoriser.load()
    scrambled = [
        r if r.split == "correction" else replace(r, category=("Travel", "Flights"))
        for r in rows
    ]
    e = embedding.TrigramEmbedder()
    assert categoriser.nearest_neighbour(scrambled, e) == categoriser.nearest_neighbour(
        rows, e
    )


def test_ece_on_assigned_rows_ignores_abstentions() -> None:
    m = categoriser.metrics_for(
        [("A", "a"), ("A", "a"), ("B", "b")],
        [(("A", "a"), 1.0), (("B", "b"), 1.0), (None, 0.0)],
    )
    assert m["coverage"] == pytest.approx(2 / 3)
    assert m["ece_assigned"] == pytest.approx(0.5)  # one right, one wrong, both at 1.0
    assert m["ece"] == pytest.approx(1 / 3)  # the abstention counts as calibrated
    assert m["A / a.recall"] == 0.5 and m["B / b.precision"] == 0.0


def test_ece_on_assigned_rows_is_zero_when_nothing_is_assigned() -> None:
    assert categoriser.metrics_for([("A", "a")], [(None, 0.0)])["ece_assigned"] == 0.0


def test_the_rules_baseline_is_versioned_by_its_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = categoriser.rules_version()
    assert before.startswith("rules-")
    assert categoriser.rules_version() == before  # deterministic
    import re

    monkeypatch.setattr(
        categorise,
        "RULES",
        (*categorise.RULES, ("x", re.compile(r"\bX\b"), ("Shopping", "Online"))),
    )
    assert categoriser.rules_version() != before


def test_committed_results_are_a_fresh_run_with_both_models() -> None:
    committed = categoriser.read_results()
    fresh = categoriser.evaluate(categoriser.load(), embedding.TrigramEmbedder())
    assert gate.differences(fresh, committed, 0.0) == []
    assert committed["dataset_version"] == categoriser.DATASET_VERSION
    assert committed["taxonomy_version"] == categorise.TAXONOMY_VERSION
    rules, nn = committed["models"]["rules"], committed["models"]["nn"]
    assert rules["model"] == "corrections+rules"
    assert rules["model_version"] == categoriser.rules_version()
    assert nn["model_version"] == embedding.TrigramEmbedder.model
    # a per-Category gate: every Category's precision and recall is a gated metric
    labels = {
        f"{t} / {s}"
        for t, s in (categoriser.resolve(r.category) for r in categoriser.load())
    }
    for model in (rules, nn):
        for label in labels:
            assert {f"{label}.precision", f"{label}.recall"} <= model["metrics"].keys()
        assert {"macro_f1", "ece", "ece_assigned", "coverage"} <= model[
            "metrics"
        ].keys()


def test_the_gate_refuses_one_category_collapsing(tmp_path: Path) -> None:
    """Macro F1 can hold while one Category collapses: the gate still refuses."""
    committed = categoriser.read_results()
    label = next(
        k for k in committed["models"]["rules"]["metrics"] if k.endswith(".recall")
    )
    worse = {
        **committed,
        "models": {
            **committed["models"],
            "rules": {
                **committed["models"]["rules"],
                "metrics": {**committed["models"]["rules"]["metrics"], label: 0.0},
            },
        },
    }
    problems = gate.lowered(worse, committed, categoriser.lower_is_better)
    assert problems and label in problems[0]


def test_ece_is_gated_lower_is_better() -> None:
    assert categoriser.lower_is_better("ece") and categoriser.lower_is_better(
        "ece_assigned"
    )
    assert not categoriser.lower_is_better("macro_f1")
    assert not categoriser.lower_is_better("Shopping / Online.recall")


def test_support_counts_every_test_row() -> None:
    committed = categoriser.read_results()
    test = [r for r in categoriser.load() if r.split == "test"]
    assert sum(committed["support"].values()) == len(test)
    assert Counter(committed["support"]).most_common(1)[0][1] >= 5


def test_a_real_category_collapse_fails_the_gate_while_macro_f1_rises() -> None:
    """Verifier #189: from the real predictions, one Category stops being
    predicted while every other row is fixed, so macro F1 goes up; the gate
    must still refuse it."""
    rows = categoriser.load()
    gold = [categoriser.resolve(r.category) for r in rows if r.split == "test"]
    preds = categoriser.baseline(rows)
    victim = Counter(gold).most_common(1)[0][0]
    worse: list[categoriser.Prediction] = [
        (None, 0.0) if g == victim else (g, 1.0) for g in gold
    ]
    before = categoriser.metrics_for(gold, preds)
    after = categoriser.metrics_for(gold, worse)
    assert after["macro_f1"] > before["macro_f1"]  # the headline hides it

    def results(m: dict[str, float]) -> gate.Results:
        return {
            "dataset_sha256": "x",
            "dataset_version": 2,
            "models": {"rules": {"metrics": m}},
        }

    problems = gate.lowered(
        results(after), results(before), categoriser.lower_is_better
    )
    assert any(f"{victim[0]} / {victim[1]}.recall" in p for p in problems), problems
