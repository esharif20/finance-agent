"""Ranking evaluation (#146): NDCG@k against the planted-relevance oracle, with
ranking by £ impact as the baseline the ranking ticket (#148) must beat."""

import json
from collections.abc import Sequence

import pytest

import gate
import ranking
import synthetic_history as sh
from ranking import Feedback, Insight


def test_the_oracle_ranking_scores_one_and_baselines_less() -> None:
    hs = sh.generate(sh.SEED, histories=2, months=sh.MIN_MONTHS)

    def oracle_for(h: sh.History) -> ranking.Ranker:
        def rank(slate: Sequence[Insight], _: Sequence[Feedback]) -> list[Insight]:
            return sorted(slate, key=lambda i: -ranking.gain(h, i))

        return rank

    for h in hs:
        assert ranking.score(h, oracle_for(h)) == {
            f"ndcg@{k}": pytest.approx(1.0) for k in ranking.KS
        }
        assert ranking.score(h, ranking.baseline)["ndcg@3"] < 1.0


def test_feedback_grows_round_by_round_and_only_covers_shown_insights() -> None:
    (h,) = sh.generate(sh.SEED, histories=1, months=sh.MIN_MONTHS)
    seen: list[int] = []

    def spy(slate: Sequence[Insight], feedback: Sequence[Feedback]) -> list[Insight]:
        seen.append(len(feedback))
        return list(slate)

    ranking.score(h, spy)
    assert seen == [ranking.SHOWN * r for r in range(ranking.ROUNDS)]


def test_a_ranker_must_return_a_permutation_of_the_slate() -> None:
    (h,) = sh.generate(sh.SEED, histories=1, months=sh.MIN_MONTHS)
    with pytest.raises(ValueError):
        ranking.score(h, lambda slate, _: list(slate)[:-1])
    with pytest.raises(ValueError):
        ranking.score(h, lambda slate, _: [slate[0], *slate[:-1]])


def test_committed_results_are_reproducible_from_the_seed() -> None:
    assert (
        gate.run(
            "ranking", ranking.evaluate, ranking.RESULTS, ranking.lower_is_better, []
        )
        == 0
    )
    committed = json.loads(ranking.RESULTS.read_text())
    base = committed["models"]["baseline"]
    assert base["model"] == "impact" and base["model_version"] is not None
    # reported per user, not only overall
    assert {f"history_{i}.ndcg@3" for i in range(committed["histories"])} <= base[
        "metrics"
    ].keys()
