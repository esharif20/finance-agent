"""Personal map evaluation (#275, docs/ML_EVALUATIONS.md): are the derived
edges right, and how well does the Engine find the Merchants a goal relates
to, for the Assistant to propose as `map_edge`s?

    uv run python evals/map.py            # CI: results reproduce
    uv run python evals/map.py --write    # refresh (see gate.py)

The truth is planted by hand in `synthetic_personas.MAP`: each row's Merchant
and Category, the Categories a goal's tags name, the Merchants a goal relates
to and the series of each confirmed life change.

Metrics, per model, micro-averaged over the personas:
- `derived_precision`, `derived_recall`: derived edges against the truth.
- `proposed_precision`, `proposed_recall`: goal - Merchant candidates
  against the planted relationships.

Baseline: a Merchant relates to a goal when a tag word occurs in its name
(co-occurrence of a Merchant and a goal's tag words).
"""

import sys
from pathlib import Path
from typing import Any

import gate
import synthetic_personas as sp
from finance_agent.engine import map as mp
from finance_agent.engine.map import Use
from finance_agent.ledger.map_edges import Node, Pair, pair

RESULTS = Path(__file__).parent / "results" / "map.json"

type Ids = tuple[dict[str, int], dict[tuple[str, str], int]]


def _ids(c: sp.MapCase) -> Ids:
    merchants = sorted({r.merchant for r in c.rows})
    cats = sorted(
        {r.category for r in c.rows} | {g for p in c.goals for g in p.categories}
    )
    return (
        {m: i for i, m in enumerate(merchants, 1)},
        {k: i for i, k in enumerate(cats, 1)},
    )


def _series(category: tuple[str, str]) -> str | None:
    return next(
        (s for s in ("rent", "bills", "groceries") if sp.in_series(category, s)), None
    )


def truth_derived(c: sp.MapCase, ids: Ids) -> set[Pair]:
    ms, cs = ids
    out: set[Pair] = set()
    for r in c.rows:
        m, k = Node("merchant", ms[r.merchant]), Node("category", cs[r.category])
        out.add(pair(m, k))
        for lid, series in enumerate(c.life_changes, 1):
            if sp.in_series(r.category, series):
                ln = Node("life_change", lid)
                out |= {pair(ln, m), pair(ln, k)}
    for gid, g in enumerate(c.goals, 1):
        out |= {pair(Node("goal", gid), Node("category", cs[k])) for k in g.categories}
    return out


def truth_proposed(c: sp.MapCase, ids: Ids) -> set[tuple[int, int]]:
    ms = ids[0]
    return {(gid, ms[m]) for gid, g in enumerate(c.goals, 1) for m in g.merchants}


def engine_derived(c: sp.MapCase, ids: Ids) -> set[Pair]:
    ms, cs = ids
    uses = [Use(ms[r.merchant], cs[r.category], _series(r.category)) for r in c.rows]
    return mp.derived(
        uses,
        {i: k for k, i in cs.items()},
        {gid: g.tags for gid, g in enumerate(c.goals, 1)},
        dict(enumerate(c.life_changes, 1)),
    )


def engine_proposed(c: sp.MapCase, ids: Ids) -> set[tuple[int, int]]:
    ms, cs = ids
    edges = engine_derived(c, ids)
    names = {i: m for m, i in ms.items()}
    cats = {i: k for k, i in cs.items()}
    return {
        (gid, mid)
        for gid, g in enumerate(c.goals, 1)
        for mid in mp.candidates(g.tags, names, cats, edges)
    }


def baseline_proposed(c: sp.MapCase, ids: Ids) -> set[tuple[int, int]]:
    """A tag word anywhere in the Merchant's name (so "car" in "CARDIFF")."""
    return {
        (gid, mid)
        for gid, g in enumerate(c.goals, 1)
        for m, mid in ids[0].items()
        if any(w in m.casefold() for t in g.tags for w in t.split("-"))
    }


def _pr(got: set[Any], want: set[Any]) -> tuple[int, int, int]:
    return len(got & want), len(got), len(want)


def _ratio(n: int, d: int) -> float:
    return round(n / d, 6) if d else 1.0


def score(derive: Any, propose: Any, cs: list[sp.MapCase]) -> dict[str, float]:
    tot = {"derived": [0, 0, 0], "proposed": [0, 0, 0]}
    for c in cs:
        ids = _ids(c)
        pairs: list[tuple[str, set[Any], set[Any]]] = [
            ("proposed", propose(c, ids), truth_proposed(c, ids))
        ]
        if derive is not None:
            pairs.append(("derived", derive(c, ids), truth_derived(c, ids)))
        for name, got, want in pairs:
            tot[name] = [a + b for a, b in zip(tot[name], _pr(got, want), strict=True)]
    out: dict[str, float] = {}
    for name, (hit, got, want) in tot.items():
        if name == "derived" and derive is None:
            continue
        out[f"{name}_precision"] = _ratio(hit, got)
        out[f"{name}_recall"] = _ratio(hit, want)
    return out


def evaluate() -> dict[str, Any]:
    cs = sp.map_cases()
    names = {
        "map": (
            "engine.map derived and candidates",
            "map-1",
            engine_derived,
            engine_proposed,
        ),
        "cooccurrence": (
            "tag word in the Merchant name",
            "baseline",
            None,
            baseline_proposed,
        ),
    }
    return {
        "harness": "map",
        "dataset": "synthetic_personas.MAP",
        "dataset_version": sp.MAP_VERSION,
        "dataset_sha256": sp.map_fingerprint(cs),
        "personas": len(cs),
        "planted_goal_merchants": sum(len(g.merchants) for c in cs for g in c.goals),
        "models": {
            key: {"model": model, "model_version": version, "metrics": score(d, p, cs)}
            for key, (model, version, d, p) in names.items()
        },
    }


if __name__ == "__main__":
    sys.exit(gate.run("map", evaluate, RESULTS, lambda _: False, sys.argv[1:]))
