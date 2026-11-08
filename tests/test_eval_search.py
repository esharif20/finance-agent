"""Search evaluation (#253): the dataset is seeded and big enough, the scorer
counts what it says, and the committed results reproduce with RRF beating
both single-list baselines on MRR."""

import json

import gate
import search as harness
import synthetic_search as ss


def test_dataset_is_seeded_with_every_query_type() -> None:
    specs = ss.generate()
    assert ss.fingerprint(specs) == ss.fingerprint(ss.generate())
    queries = [q for s in specs for q in s.queries]
    assert len(queries) >= 150
    assert {q.type for q in queries} == set(ss.QUERY_TYPES)
    for s in specs:
        names = [s.rows[t][0] for t in s.things]
        assert len(set(names)) == len(names)  # each thing is one Transaction
        for q in s.queries:
            if q.type == "misspelling":
                assert q.text.upper() not in names


def test_scorer_on_hand_rankings() -> None:
    q = ss.Query("exact", "x", ss.Target("thing", 0))
    perfect = harness.score([(q, {1, 2}, [1, 2, 3])])
    assert perfect["overall.recall@5"] == 1.0 and perfect["exact.mrr"] == 1.0
    late = harness.score([(q, {1, 2}, [9, 8, 7, 6, 5, 2, 1])])
    assert late["overall.recall@5"] == 0.0 and late["overall.recall@10"] == 1.0
    assert late["overall.mrr"] == round(1 / 6, 6)
    assert harness.score([(q, {1}, [])])["overall.mrr"] == 0.0


def test_committed_results_reproduce_and_rrf_beats_both_baselines() -> None:
    fresh = json.loads(json.dumps(harness.evaluate()))
    committed = json.loads(harness.RESULTS.read_text())
    assert gate.differences(fresh, committed, 0.0) == []
    m = {k: v["metrics"] for k, v in committed["models"].items()}
    assert m["hybrid"]["overall.mrr"] > m["fts"]["overall.mrr"]
    assert m["hybrid"]["overall.mrr"] > m["vector"]["overall.mrr"]
    assert m["hybrid"]["overall.recall@10"] >= 0.95


def test_hybrid_latency_is_interactive() -> None:
    _, p95 = harness.run(harness.TrigramEmbedder())
    assert p95 < 250  # ms; generous for a loaded CI runner
