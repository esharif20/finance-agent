"""Categoriser evaluation (#112): the synthetic dataset, the Corrections + rules
baseline and the regression gate CI runs."""

import categoriser
from categoriser import Row
from finance_agent import categorise


def _leaves() -> set[tuple[str, str]]:
    return {(top, sub) for top, subs in categorise.TAXONOMY.items() for sub in subs}


def test_committed_dataset_is_the_seeded_generator_output() -> None:
    text = categoriser.DATASET.read_text()
    assert text.startswith("# synthetic\n")
    assert categoriser.generate(categoriser.SEED) == text


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
    eats = ("Food & Drink", "Takeaway")
    rows = [Row(1, "test", "lookalike", "UBER EATS", -900, eats)]
    assert categoriser.baseline(rows) == [(("Transport", "Taxi"), 1.0)]


def test_committed_results_match_a_fresh_run() -> None:
    committed = categoriser.read_results()
    fresh = categoriser.evaluate(categoriser.load())
    assert categoriser.regressions(fresh, committed) == []
    assert fresh["model"] == committed["model"] == "corrections+rules"
    assert fresh["seed"] == categoriser.SEED


def test_a_worse_score_or_a_changed_dataset_is_a_regression() -> None:
    committed = categoriser.read_results()
    worse_f1 = {**committed, "macro_f1": committed["macro_f1"] - 0.01}
    worse_ece = {**committed, "ece": committed["ece"] + 0.01}
    other_data = {**committed, "dataset_sha256": "0" * 64}
    better = {**committed, "macro_f1": committed["macro_f1"] + 0.01}
    assert len(categoriser.regressions(worse_f1, committed)) == 1
    assert len(categoriser.regressions(worse_ece, committed)) == 1
    assert len(categoriser.regressions(other_data, committed)) == 1
    assert categoriser.regressions(better, committed) == []
