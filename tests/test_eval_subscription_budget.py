"""Subscription budget evaluation (#308): the committed results reproduce from
the seed, the advisor meets its hard gates and beats both baselines."""

import json

import gate
import subscription_budget as ev


def test_dataset_is_seeded_and_holds_every_case() -> None:
    ps = ev.portfolios()
    assert ev.fingerprint(ps) == ev.fingerprint(ev.portfolios())
    none_fit = sum(all(s.monthly > p.cap_minor for s in p.subs) for p in ps)
    under = sum(sum(s.monthly for s in p.subs) <= p.cap_minor for p in ps)
    assert 0 < none_fit < len(ps) and 0 < under < len(ps)
    assert any(len({s.kind for s in p.subs}) < len(p.subs) for p in ps)  # overlaps


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run("subscription_budget", ev.evaluate, ev.RESULTS, ev.lower_is_better, [])
        == 0
    )


def test_advisor_meets_its_gates_and_beats_both_baselines() -> None:
    models = json.loads(ev.RESULTS.read_text())["models"]
    m = models["advisor"]["metrics"]
    assert m["cap_respected"] == 1.0
    assert m["consolidate_duplicates"] == 0
    assert m["figures_consistent"] == 1.0
    assert m["nothing_fits_reported"] == 1.0 and m["false_nothing_fits"] == 0
    for base in ("cancel_most_expensive", "cancel_newest"):
        assert m["value_kept"] > models[base]["metrics"]["value_kept"]
