"""Contextual bandit evaluation (#272, docs/ML_EVALUATIONS.md): does Thompson
sampling on (type, Category, time of month, spend) find what each user
accepts, against the live ranker and £ impact? And can off-policy evaluation
on the live ranker's log tell its value before it ever serves?

    uv run python evals/bandit.py            # CI: results reproduce
    uv run python evals/bandit.py --write    # refresh (see gate.py)

Dataset `synthetic_bandit`: planted, context-dependent acceptance. Each week
a policy shows `K` of the week's candidates and learns from what was accepted
or rejected (never anything else).

Metrics per policy over 26 weeks:
- `acceptance`: mean planted acceptance probability of a shown item (top 3);
- `regret`: per user, the cumulative shortfall in expected acceptances
  against the oracle that shows the week's three most acceptable items.

Off-policy evaluation (candidate only): the live ranker serves with the
seeded epsilon slate of `engine.ope` and the bandit runs in shadow, learning
from the live log. IPS and SNIPS on the logged propensities and the bandit's
slate-membership probabilities estimate its value per slot; `ope_*_error` is
the absolute error against its true value, known from the plant.
"""

import sys
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import gate
import synthetic_bandit as sb
from finance_agent.engine import bandit, ope, ranking

RESULTS = Path(__file__).parent / "results" / "bandit.json"
K = 3

type Policy = Callable[[int, sb.Week], list[sb.Item]]
type Learn = Callable[[sb.Week, sb.Item, bool], None]


def _arms(w: sb.Week) -> list[bandit.Arm]:
    return [bandit.Arm(i.id, i.type, i.category) for i in w.items]


def _outcome(accepted: bool) -> str:
    return "accepted" if accepted else "rejected"


def thompson(u: sb.User) -> tuple[Policy, Learn]:
    seen: list[bandit.Observed] = []

    def pick(n: int, w: sb.Week) -> list[sb.Item]:
        d = bandit.choose(
            _arms(w), bandit.learn(seen), w.context, K, ope.seed(u.name, n)
        )
        by_id = {i.id: i for i in w.items}
        return [by_id[p.arm.item] for p in d.slate]

    def learn(w: sb.Week, i: sb.Item, ok: bool) -> None:
        seen.append(bandit.Observed(i.type, i.category, w.context, _outcome(ok)))

    return pick, learn


def live(u: sb.User) -> tuple[Policy, Learn]:
    signals: list[tuple[str, str, bool]] = []

    def pick(n: int, w: sb.Week) -> list[sb.Item]:
        return ranking.rank(w.items, ranking.posteriors(signals))[:K]

    def learn(w: sb.Week, i: sb.Item, ok: bool) -> None:
        signals.append((i.type, i.category, ok))

    return pick, learn


def impact(u: sb.User) -> tuple[Policy, Learn]:
    def pick(n: int, w: sb.Week) -> list[sb.Item]:
        return sorted(w.items, key=lambda i: -i.impact)[:K]

    def learn(w: sb.Week, i: sb.Item, ok: bool) -> None:
        pass  # £ impact learns nothing

    return pick, learn


def _best(w: sb.Week) -> float:
    return sum(sorted((i.p for i in w.items), reverse=True)[:K])


def simulate(make: Callable[[sb.User], tuple[Policy, Learn]]) -> dict[str, float]:
    gained = regret = 0.0
    shown = largest = 0
    us = sb.users()
    for u in us:
        pick, learn = make(u)
        for n, w in enumerate(u.weeks):
            slate = pick(n, w)
            largest = max(largest, len(slate))
            got = sum(i.p for i in slate)
            gained, shown, regret = (
                gained + got,
                shown + len(slate),
                regret + _best(w) - got,
            )
            for i in slate:
                learn(w, i, i.luck < i.p)
    return {
        "acceptance": round(gained / shown, 6),
        "regret": round(regret / len(us), 6),
        "largest_slate": largest,
    }


def off_policy() -> dict[str, float]:
    """The live ranker logs with exploration; the bandit runs in shadow."""
    logs: list[ope.Logged] = []
    truth = 0.0
    for u in sb.users():
        signals: list[tuple[str, str, bool]] = []
        seen: list[bandit.Observed] = []
        for n, w in enumerate(u.weeks):
            ranked = ranking.rank(w.items, ranking.posteriors(signals))
            shadow = bandit.choose(
                _arms(w), bandit.learn(seen), w.context, K, ope.seed(u.name, n, "ts")
            )
            q = shadow.membership
            truth += sum(float(q[i.id]) * i.p for i in w.items)
            for s in ope.slate(ranked, K, ope.seed(u.name, n, ope.POLICY)):
                ok = s.item.luck < s.item.p
                logs.append(ope.Logged(Fraction(ok), s.propensity, q[s.item.id]))
                signals.append((s.item.type, s.item.category, ok))
                seen.append(
                    bandit.Observed(
                        s.item.type, s.item.category, w.context, _outcome(ok)
                    )
                )
    true_value = truth / len(logs)
    return {
        "ope_true_value": round(true_value, 6),
        "ope_ips_error": round(abs(float(ope.ips(logs) or 0) - true_value), 6),
        "ope_snips_error": round(abs(float(ope.snips(logs) or 0) - true_value), 6),
        "ope_ess": round(float(ope.ess(logs)), 6),
    }


MODELS: dict[str, tuple[str, str, Callable[[sb.User], tuple[Policy, Learn]]]] = {
    "candidate": ("Thompson sampling (engine.bandit)", bandit.POLICY, thompson),
    "live_ranker": ("impact x confidence x Beta relevance", "rank-v1", live),
    "impact": ("rank by £ impact", "impact-v1", impact),
}


def evaluate() -> dict[str, Any]:
    us = sb.users()
    models: dict[str, Any] = {
        key: {"model": name, "model_version": version, "metrics": simulate(make)}
        for key, (name, version, make) in MODELS.items()
    }
    models["candidate"]["metrics"] |= off_policy()
    return {
        "harness": "bandit",
        "dataset": "synthetic_bandit",
        "dataset_version": sb.DATASET_VERSION,
        "dataset_sha256": sb.fingerprint(us),
        "seed": sb.SEED,
        "users": len(us),
        "weeks": sb.WEEKS,
        "candidates": sb.CANDIDATES,
        "shown": K,
        "cap": bandit.CAP,
        "draws": bandit.DRAWS + 1,
        "models": models,
    }


def lower_is_better(name: str) -> bool:
    return name.endswith(("_error", "regret", "largest_slate"))


if __name__ == "__main__":
    # Beta draws are float: another platform's libm may move the last digits
    sys.exit(gate.run("bandit", evaluate, RESULTS, lower_is_better, sys.argv[1:], 1e-6))
