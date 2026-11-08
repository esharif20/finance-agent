"""Subscription budget advisor evaluation (#308, docs/ML_EVALUATIONS.md): do
the options respect the cap, consolidate overlaps, keep their figures exact,
and keep more value than the obvious rules?

    uv run python evals/subscription_budget.py            # CI: results reproduce
    uv run python evals/subscription_budget.py --write    # refresh (see gate.py)

Dataset: seeded synthetic portfolios of one group, each Subscription with a
gold utility the advisor never sees. It sees only signals drawn from it: a
noisy preference strength (70%), a worth-it answer (30%, 10% flipped), price
rises, converted trials and inactive services (whose gold utility is low).
A second service of one kind is worth half (they overlap). Caps run from below
the cheapest service to above the total.

Metrics:

- `cap_respected`: options whose new total is within the cap (must be 1.0).
- `consolidate_duplicates`: consolidate options keeping two of a kind (0).
- `figures_consistent`: options whose total, saving and cancellations equal
  the sums of their own steps (must be 1.0).
- `nothing_fits_reported`: portfolios where every service exceeds the cap,
  reported as nothing fits (1.0), and `false_nothing_fits` the reverse (0).
- `value_kept`: gold utility kept / the best possible under the cap, mean over
  portfolios where something fits. For the advisor, its `most_valued` option;
  `value_kept_best` is the best of its options. Baselines cancel the most
  expensive, or the newest, first until the total fits.
"""

import hashlib
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from typing import Any

import gate
from finance_agent.categorise.subscriptions import display
from finance_agent.engine import subscription_budget as sb

RESULTS = Path(__file__).parent / "results" / "subscription_budget.json"
DATASET_VERSION = 1
SEED = 308
PORTFOLIOS = 240
KINDS = ("ai_chat", "ai_chat", "ai_coding", "ai_image", None)
LOWER = {"consolidate_duplicates", "false_nothing_fits"}


@dataclass(frozen=True)
class Portfolio:
    subs: tuple[sb.Sub, ...]
    gold: tuple[Fraction, ...]  # latent utility, in the order of `subs`
    cap_minor: int


def _fr(x: float) -> Fraction:
    return Fraction(str(round(x, 2)))


def portfolio(i: int) -> Portfolio:
    rng = random.Random(SEED * 1000 + i)  # noqa: S311 - synthetic data
    subs: list[sb.Sub] = []
    gold: list[Fraction] = []
    for m in range(rng.randint(2, 9)):
        u = rng.uniform(0.5, 5.0)
        inactive, trial = rng.random() < 0.1, rng.random() < 0.1
        u *= 0.3 if inactive else 0.7 if trial else 1.0
        pence = rng.randint(399, 3999)
        monthly = (
            Fraction(pence * 12 - rng.randint(0, 2000), 12)
            if rng.random() < 0.2
            else Fraction(pence)
        )
        worth = None
        if rng.random() < 0.3:
            worth = (u > 2.5) != (rng.random() < 0.1)
        subs.append(
            sb.Sub(
                merchant_id=m + 1,
                name=f"SYNTH {m + 1}",
                monthly=monthly,
                kind=rng.choice(KINDS),
                months_held=rng.randint(0, 40),
                price_rise=rng.random() < 0.2,
                inactive=inactive,
                trial_converted=trial,
                worth_it=worth,
                strength=_fr(0.8 * (u - 2.5) + rng.uniform(-0.6, 0.6))
                if rng.random() < 0.7
                else None,
            )
        )
        gold.append(_fr(u))
    total = sum((s.monthly for s in subs), Fraction(0))
    share = rng.choice((0.02, 0.3, 0.5, 0.7, 0.9, 1.2))
    return Portfolio(tuple(subs), tuple(gold), int(total * _fr(share)))


def portfolios() -> list[Portfolio]:
    return [portfolio(i) for i in range(PORTFOLIOS)]


def fingerprint(ps: Sequence[Portfolio]) -> str:
    return hashlib.sha256(repr(ps).encode()).hexdigest()


def gold_value(p: Portfolio, kept: Sequence[int]) -> Fraction:
    """Gold utility of the kept Subscriptions: each kind's best counts in
    full, every further one of that kind at half."""
    by_kind: dict[object, list[Fraction]] = {}
    for i in kept:
        k = p.subs[i].kind
        by_kind.setdefault(k if k is not None else ("own", i), []).append(p.gold[i])
    return sum(
        (
            u if r == 0 else u / 2
            for us in by_kind.values()
            for r, u in enumerate(sorted(us, reverse=True))
        ),
        Fraction(0),
    )


def _fits(p: Portfolio, kept: Sequence[int]) -> bool:
    return sum((p.subs[i].monthly for i in kept), Fraction(0)) <= p.cap_minor


def optimum(p: Portfolio) -> Fraction:
    n = len(p.subs)
    return max(
        gold_value(p, c)
        for r in range(n + 1)
        for c in combinations(range(n), r)
        if _fits(p, c)
    )


def _cancel_first(order: Callable[[sb.Sub], Any]) -> Callable[[Portfolio], list[int]]:
    def kept(p: Portfolio) -> list[int]:
        keep = list(range(len(p.subs)))
        for i in sorted(keep, key=lambda i: order(p.subs[i])):
            if _fits(p, keep):
                break
            keep.remove(i)
        return keep

    return kept


BASELINES = {
    "cancel_most_expensive": _cancel_first(lambda s: (-s.monthly, s.merchant_id)),
    "cancel_newest": _cancel_first(lambda s: (s.months_held, s.merchant_id)),
}


def _kept(o: sb.Option) -> list[int]:
    return [i for i, s in enumerate(o.steps) if s.action != "cancel"]


def _consistent(a: sb.Advice, o: sb.Option) -> bool:
    kept = [s for s in o.steps if s.action != "cancel"]
    exact = sum((s.sub.monthly for s in kept), Fraction(0))
    return (
        o.total_minor == display(exact)
        and o.saving_minor == a.current_minor - o.total_minor
        and o.cancellations == len(o.steps) - len(kept)
        and all(s.monthly_minor == display(s.sub.monthly) for s in o.steps)
    )


def _mean(xs: list[Fraction]) -> float:
    return round(float(sum(xs, Fraction(0)) / len(xs)), 6) if xs else 1.0


def _rate(ok: list[bool]) -> float:
    return round(sum(ok) / len(ok), 6) if ok else 1.0


def score_advisor(ps: Sequence[Portfolio]) -> dict[str, float | int]:
    respected: list[bool] = []
    consistent: list[bool] = []
    reported: list[bool] = []
    kept_mv: list[Fraction] = []
    kept_best: list[Fraction] = []
    duplicates = false_none = 0
    for p in ps:
        a = sb.advise(p.subs, p.cap_minor)
        none_fit = all(s.monthly > p.cap_minor for s in p.subs)
        if none_fit:
            reported.append(a.nothing_fits)
            continue
        false_none += a.nothing_fits
        best = optimum(p)
        for o in a.options:
            k = _kept(o)
            respected.append(_fits(p, k) and o.total_minor <= p.cap_minor)
            consistent.append(_consistent(a, o))
            if o.name == "consolidate":
                kinds = [p.subs[i].kind for i in k if p.subs[i].kind is not None]
                duplicates += len(kinds) != len(set(kinds))
        mv = next(o for o in a.options if "most_valued" in (o.name, *o.also))
        kept_mv.append(gold_value(p, _kept(mv)) / best if best else Fraction(1))
        top = max(gold_value(p, _kept(o)) for o in a.options)
        kept_best.append(top / best if best else Fraction(1))
    return {
        "cap_respected": _rate(respected),
        "consolidate_duplicates": duplicates,
        "figures_consistent": _rate(consistent),
        "nothing_fits_reported": _rate(reported),
        "false_nothing_fits": false_none,
        "value_kept": _mean(kept_mv),
        "value_kept_best": _mean(kept_best),
    }


def score_baseline(
    fn: Callable[[Portfolio], list[int]], ps: Sequence[Portfolio]
) -> dict[str, float]:
    kept: list[Fraction] = []
    respected: list[bool] = []
    for p in ps:
        if all(s.monthly > p.cap_minor for s in p.subs):
            continue
        k = fn(p)
        best = optimum(p)
        respected.append(_fits(p, k))
        kept.append(gold_value(p, k) / best if best else Fraction(1))
    return {"cap_respected": _rate(respected), "value_kept": _mean(kept)}


def evaluate() -> dict[str, Any]:
    ps = portfolios()
    return {
        "harness": "subscription_budget",
        "dataset": "synthetic subscription portfolios (in this file)",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": fingerprint(ps),
        "portfolios": len(ps),
        "nothing_fits_portfolios": sum(
            all(s.monthly > p.cap_minor for s in p.subs) for p in ps
        ),
        "models": {
            "advisor": {
                "model": "engine.subscription_budget exhaustive subset search",
                "model_version": "subset-1",
                "metrics": score_advisor(ps),
            }
        }
        | {
            key: {
                "model": key.replace("_", " "),
                "model_version": "baseline",
                "metrics": score_baseline(fn, ps),
            }
            for key, fn in BASELINES.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name in LOWER


if __name__ == "__main__":
    sys.exit(
        gate.run(
            "subscription_budget", evaluate, RESULTS, lower_is_better, sys.argv[1:]
        )
    )
