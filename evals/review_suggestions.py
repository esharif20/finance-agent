"""Review suggestions evaluation (the 461-as-Groceries bug, docs/ML_EVALUATIONS.md).

The real case, synthetic: the user's bulk Groceries Corrections were reverted,
leaving ten, all Groceries. The old suggester ran kNN over Correction-labelled
Merchants only, with no floor, so every Merchant's nearest label was a grocer
and one bulk card offered "Accept 461 as Groceries".

Targets: what Corrections, rules and the UK base leave Uncategorised on the
synthetic categoriser set (v4), plus PAYEES, a synthetic mix of payee types
the set lacks (people, government, tuition, AI services, dramas apps and
plainly named independents). A person's gold is "no suggestion".

    uv run python evals/review_suggestions.py           # CI: results reproduce
    uv run python evals/review_suggestions.py --write   # regenerate

Models (top suggestion only, which is what a bulk card groups by):
- "old": kNN over the ten Groceries Corrections, no floor (the bug).
- "new": `auto.choose` as the Review Queue runs it: payee-type and lexicon
  rules, zero-shot, kNN over base Merchants + Corrections behind the floor and
  the label-diversity check, then the stored LLM answer (the committed
  categoriser_gate signals; none for PAYEES).
- "lexicon", "zero_shot": each tier alone on the categoriser residual, for
  coverage and precision.
- "new_nomic": the new chain on the committed nomic-embed-text kNN signals
  (labels as in that run) and LLM answers: what the app sees with Ollama on.

Metrics: `wrong_rate` (a wrong top suggestion, per target: what a bulk accept
would file wrongly), `false_groceries` (top suggestion Groceries, gold not),
`accuracy` (right, per suggested), `coverage` (suggested, per target),
`person_suggested` (people offered any Category). The gate: false_groceries
about 0, and the categoriser gate unchanged (its harness runs separately).
"""

import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import categoriser as cz
import categoriser_gate as cg
import gate
from finance_agent import categorise
from finance_agent.categorise import auto, embedding, llm, payees, zero_shot

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "review_suggestions.json"
SIGNALS = cg.results_path("qwen3.5:4b-q4_K_M")
DATASET_VERSION = 1
FEW = 10  # the Corrections left after the revert, all Groceries
GROCERIES: tuple[str, str] = ("Food & Drink", "Groceries")
OTHER_BILLS: tuple[str, str] = ("Bills", "Other bills")
ZERO_SHOT_PRECISION = 90  # percent: a zero-shot suggestion must beat the LLM's
type Category = tuple[str, str]

# Synthetic payees (descriptor, gold; None: a person, no suggestion). No real
# names: invented surnames, public brand names only.
PAYEES: tuple[tuple[str, Category | None], ...] = (
    ("J QWERTON", None),
    ("MR A ZELLBY", None),
    ("MRS K DRAVEN", None),
    ("SARAH VOLKANE", None),
    ("MOHAMMED QARRISH", None),
    ("PRIYA OSTRAVEL", None),
    ("T BRANNIGOLD", None),
    ("FPO LEO MARTWICK", None),
    ("STANDING ORDER R PELLINGHAM", None),
    ("DAVID ORMSWALL", None),
    ("KWAME ASANTVILLE", None),
    ("TEMIDAYO ARKWRIGHTON", None),  # not in the first-name list: a known miss
    ("DWP DEBT MANAGEMENT", OTHER_BILLS),
    ("DEPT FOR WORK PENSIONS", OTHER_BILLS),
    ("HMCTS FINE PAYMENT", OTHER_BILLS),
    ("FLYWIRE UNIVERSITY TUITION", OTHER_BILLS),
    ("FLYWIRE PAYMENTS", OTHER_BILLS),
    ("UNIVERSITY OF NORTHVALE FEES", OTHER_BILLS),
    ("MISTRAL AI", ("Subscriptions", "AI tools")),
    ("ELEVENLABS", ("Subscriptions", "AI tools")),
    ("GITHUB COPILOT", ("Subscriptions", "AI tools")),
    ("CHARACTER.AI", ("Subscriptions", "AI tools")),
    ("RAKUTEN VIKI", ("Subscriptions", "Streaming")),
    ("DRAMABOX", ("Subscriptions", "Streaming")),
    ("IQIYI", ("Subscriptions", "Streaming")),
    ("CANVA", ("Subscriptions", "Software")),
    ("FIGMA", ("Subscriptions", "Software")),
    ("JACKS FOOD AND WINE", GROCERIES),
    ("TOWN MARKET", GROCERIES),
    ("HILLTOP MINI MARKET", GROCERIES),
    ("PARADE OFF LICENCE", GROCERIES),
    ("S & J FAST FOOD", ("Food & Drink", "Takeaway")),
    ("ROYAL KEBAB HOUSE", ("Food & Drink", "Takeaway")),
    ("FIREPITT GRILL", ("Food & Drink", "Takeaway")),
    ("LA PETITE BRASSERIE", ("Food & Drink", "Eating out")),
    ("CORNER BISTRO", ("Food & Drink", "Eating out")),
)


def _merchant(descriptor: str) -> str | None:
    return categorise.classify(descriptor, -1000, "DEB", transfer=False).merchant


def targets(rows: Sequence[cz.Row]) -> dict[str, Category | None]:
    """Merchant -> gold, for every Merchant the rules leave Uncategorised."""
    out: dict[str, Category | None] = {
        m: cz.resolve(rs[0].category) for m, rs in cg.residual(rows).items()
    }
    for d, gold in PAYEES:
        c = categorise.classify(d, -1000, "DEB", transfer=False)
        if c.merchant and c.category == categorise.UNCATEGORISED:
            out[c.merchant] = gold
    return out


def _signals() -> dict[str, dict[str, Any]]:
    return json.loads(SIGNALS.read_text())["signals"]


def _cat(label: str | None) -> Category | None:
    if not label or label == llm.UNSURE:
        return None
    top, sub = label.split(" › ")
    return (top, sub)


def score(
    gold: Mapping[str, Category | None], top: Mapping[str, Category | None]
) -> dict[str, float]:
    n = len(gold)
    said = [m for m in gold if top.get(m) is not None]
    right = sum(top[m] == gold[m] for m in said)
    people = [m for m in gold if gold[m] is None]

    def r(x: float) -> float:
        return round(x, 6)

    return {
        "targets": n,
        "coverage": r(len(said) / n),
        "accuracy": r(right / len(said)) if said else 0.0,
        "wrong_rate": r((len(said) - right) / n),
        "false_groceries": r(
            sum(top[m] == GROCERIES and gold[m] != GROCERIES for m in said) / n
        ),
        "person_suggested": r(sum(top.get(m) is not None for m in people) / len(people))
        if people
        else 0.0,
    }


def person_detector(gold: Mapping[str, Category | None]) -> dict[str, float]:
    """`payees.person` against the gold: people found, businesses spared."""
    flagged = {m for m in gold if payees.person(m)}
    people = {m for m in gold if gold[m] is None}
    tp = len(flagged & people)
    return {
        "precision": round(tp / len(flagged), 6) if flagged else 1.0,
        "recall": round(tp / len(people), 6) if people else 1.0,
        "businesses_flagged": len(flagged - people),
    }


def calibrate_margin(scored: Sequence[tuple[int, bool]]) -> int | None:
    """The lowest zero-shot margin at which every band from there up keeps
    ZERO_SHOT_PRECISION% right; None when no margin does on 5+ guesses."""
    best: int | None = None
    hits = n = 0
    for s in sorted({s for s, _ in scored}, reverse=True):
        band = [ok for x, ok in scored if x == s]
        hits, n = hits + sum(band), n + len(band)
        if hits * 100 < ZERO_SHOT_PRECISION * n:
            break
        if n >= 5:
            best = s
    return best


def evaluate(rows: Sequence[cz.Row], embedder: embedding.Embedder) -> dict[str, Any]:
    gold = targets(rows)
    known = cz.known(rows)  # base Merchants + every Correction Merchant
    base = {
        m: c
        for m, c in known.items()
        if categorise.classify(m, -1000, "DEB", transfer=False).source.startswith(
            "merchant:"
        )
    }
    groceries = sorted(m for m, c in known.items() if c == GROCERIES and m not in base)
    few = {m: GROCERIES for m in groceries[:FEW]}
    labels = {**base, **few}  # what the app now learns from, after the revert
    names = sorted(set(gold) | set(labels))
    vec = dict(zip(names, embedder.embed(names), strict=True))
    sig = _signals()

    def ranked(m: str, by: Mapping[str, Category]) -> list[tuple[int, Category]]:
        return embedding.rank(
            vec[m], [(vec[n], {c}) for n, c in sorted(by.items()) if n != m]
        )

    old = {m: (h[0][1] if (h := ranked(m, few)) else None) for m in gold}

    # the zero-shot tier, calibrated on the categoriser residual
    model = embedder.model
    zero_shot._cache.pop(model, None)  # pyright: ignore[reportPrivateUsage]
    zero_shot.prime(model, embedder)
    ls = zero_shot.labels(model) or {}
    residual = {m: g for m, g in gold.items() if m in sig}
    zs = {m: zero_shot.nearest(vec[m], ls) for m in residual}
    margin = calibrate_margin([(gap, c == residual[m]) for m, (gap, c) in zs.items()])
    zero = {m: c for m, (gap, c) in zs.items() if margin is not None and gap >= margin}

    by_label = {m: {c} for m, c in labels.items()}
    floor = (
        auto.FLOORS.get(model)
        if auto.diverse(dict(enumerate(by_label.values())))
        else None
    )

    def zero_for(m: str) -> Category | None:
        if margin is None:
            return None
        gap, c = zero_shot.nearest(vec[m], ls)
        return c if gap >= margin else None

    def first(xs: list[Category]) -> Category | None:
        return xs[0] if xs else None

    new = {
        m: first(
            auto.choose(
                m,
                ranked(m, labels)[: embedding.SUGGESTIONS],
                floor,
                zero_for(m),
                _cat(sig[m]["llm"]) if m in sig else None,
            )
        )
        for m in gold
    }
    # the same chain when the ten Groceries Corrections are the only labels:
    # the diversity check must keep the kNN out
    few_floor = (
        auto.FLOORS.get(model)
        if auto.diverse(dict(enumerate({c} for c in few.values())))
        else None
    )
    few_only = {
        m: first(
            auto.choose(
                m,
                ranked(m, few)[: embedding.SUGGESTIONS],
                few_floor,
                zero_for(m),
                _cat(sig[m]["llm"]) if m in sig else None,
            )
        )
        for m in gold
    }
    lexicon = {m: payees.rule(m) for m in residual}
    nomic_floor = auto.FLOORS["nomic-embed-text"]
    nomic = {
        m: first(
            auto.choose(
                m,
                [(s["knn"][0], c)] if s["knn"] and (c := _cat(s["knn"][1])) else [],
                nomic_floor,
                None,
                _cat(s["llm"]),
            )
        )
        for m, s in sig.items()
    }
    sig_gold = {m: _cat(s["gold"]) for m, s in sig.items()}
    models: dict[str, Any] = {
        "old": {
            "model": "knn over Corrections only, no floor",
            "metrics": score(gold, old),
        },
        "new": {
            "model": "payee rules, zero-shot, floored knn, stored LLM",
            "metrics": score(gold, new),
        },
        "new_few_labels": {
            "model": "the new chain, Groceries Corrections the only labels",
            "metrics": score(gold, few_only),
        },
        "lexicon": {
            "model": "payee-type and lexicon rules alone",
            "metrics": score(residual, lexicon),
        },
        "zero_shot": {
            "model": "zero-shot nearest description",
            "metrics": score(residual, zero),
        },
        "new_nomic": {
            "model": "the new chain on nomic signals",
            "metrics": score(sig_gold, nomic),
        },
    }
    for v in models.values():
        v["model_version"] = model
    data = repr((sorted(gold.items()), FEW, PAYEES)).encode()
    return {
        "harness": "review_suggestions",
        "dataset": "categoriser_v4.csv + PAYEES",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(data).hexdigest(),
        "signals": SIGNALS.name,
        "labels": {
            "corrections": len(few),
            "base": len(base),
            "diverse": floor is not None,
        },
        "calibration": {
            "knn_floor": floor,
            "zero_shot_margin": margin,
            "zero_shot_target_precision": ZERO_SHOT_PRECISION / 100,
        },
        "person_detector": person_detector(gold),
        "cascade": cascade(rows, sig, nomic),
        "models": models,
    }


BATCH = 20  # Merchants per LLM prompt in a batched fallback


def cascade(
    rows: Sequence[cz.Row],
    sig: Mapping[str, Mapping[str, Any]],
    residual_pick: Mapping[str, Category | None],
) -> dict[str, Any]:
    """The 98% target, measured, not fitted: every test Merchant through the
    full chain with Ollama on. Confident (`auto` tier): Corrections, rules and
    the UK base. The rest: the new chain on the committed nomic kNN and LLM
    signals (`residual_pick`). Merchant-level; LLM calls are the Merchants
    that reach the LLM, per 100 Merchants, one call each or BATCH a call."""
    test = [r for r in rows if r.split == "test"]
    confident: dict[str, tuple[Category | None, Category]] = {}
    for row, (p, _) in zip(test, cz.baseline(rows), strict=True):
        if (m := _merchant(row.descriptor)) and p is not None:
            confident.setdefault(m, (p, cz.resolve(row.category)))
    merchants = len(confident) + len(sig)
    auto_right = sum(p == g for p, g in confident.values())
    picked = {m: c for m, c in residual_pick.items() if c is not None}
    low_right = sum(c == _cat(sig[m]["gold"]) for m, c in picked.items())
    llm_reached = sum(
        1
        for m, s in sig.items()
        if payees.rule(m) is None
        and not payees.person(m)
        and not (s["knn"] and s["knn"][0] >= auto.FLOORS["nomic-embed-text"])
    )
    assigned = len(confident) + len(picked)

    def r(x: float) -> float:
        return round(x, 6)

    return {
        "merchants": merchants,
        "coverage": r(assigned / merchants),
        "accuracy_assigned": r((auto_right + low_right) / assigned),
        "auto_precision": r(auto_right / len(confident)),
        "auto_coverage": r(len(confident) / merchants),
        "low_accuracy": r(low_right / len(picked)) if picked else 0.0,
        "llm_calls_per_100_unbatched": r(100 * llm_reached / merchants),
        "llm_calls_per_100_batched": r(100 * -(-llm_reached // BATCH) / merchants),
        "note": "kNN floor 720 was calibrated on this test split (categoriser_ollama)",
    }


def lower_is_better(name: str) -> bool:
    return name in ("wrong_rate", "false_groceries", "person_suggested")


def main(argv: Sequence[str]) -> int:
    run: Callable[[], dict[str, Any]] = lambda: evaluate(  # noqa: E731
        cz.load(), embedding.TrigramEmbedder()
    )
    return gate.run("review_suggestions", run, RESULTS, lower_is_better, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
