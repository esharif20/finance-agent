"""Semantic resolution evaluation (#317): the user's fuel-total doubt.

A synthetic Ledger of bank-style Merchant names, some filed under the wrong
Category (a petrol station under Shopping, a pharmacy under Household).
Two tasks, scored with `assistant.semantic`'s own decision functions:

- filed: for a Category the resolver reads ("fuel"), which Merchants filed
  elsewhere also belong to it. Gold: the concept's Merchants not filed there.
  precision / recall over (Category, Merchant) pairs.
- term: a word no resolver reads ("forecourt", "chemist") to its Category,
  or to none ("credit score"). accuracy, and false_category (a Category for a
  term whose gold is none, per such term).
- term_merchants: the Merchants a term's vector ranks (above the threshold)
  fused with FTS5 BM25 by RRF. precision / recall against the concept.

Signals are nomic-embed-text similarities (thousandths) of each name and term
to each description and of each term to each Merchant, committed in
evals/datasets/semantic_signals.json so CI reproduces without Ollama. The
thresholds are swept on those signals: the best recall at precision >= 0.9.

    uv run python evals/semantic_resolution.py            # CI: results reproduce
    uv run python evals/semantic_resolution.py --write    # rewrite results
    uv run python evals/semantic_resolution.py --record   # re-embed (Ollama on)
"""

import functools
import hashlib
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import gate
from finance_agent.assistant import resolve as rs
from finance_agent.assistant import semantic
from finance_agent.categorise import embedding, zero_shot
from finance_agent.ledger import search_index

HERE = Path(__file__).parent
SIGNALS = HERE / "datasets" / "semantic_signals.json"
RESULTS = HERE / "results" / "semantic_resolution.json"
DATASET_VERSION = 1
MODEL = "nomic-embed-text"
MIN_PRECISION = 0.9

# synthetic: bank-style names, the Category each is filed under (None:
# Uncategorised), and the concept it truly belongs to
MERCHANTS: list[tuple[str, str | None, str]] = [
    ("BP CONNECT A40", "Transport > Fuel", "fuel"),
    ("SHELL OXFORD ROAD", "Transport > Fuel", "fuel"),
    ("ESSO MAIDENHEAD", "Shopping > Household", "fuel"),
    ("TEXACO SERVICE STATION", None, "fuel"),
    ("JET PETROL FORECOURT", "Shopping > Online", "fuel"),
    ("MFG FUEL STATION", "Food & Drink > Groceries", "fuel"),
    ("COSTA COFFEE", "Food & Drink > Coffee", "coffee"),
    ("STARBUCKS COFFEE", "Food & Drink > Coffee", "coffee"),
    ("CAFFE NERO", "Food & Drink > Eating out", "coffee"),
    ("ESPRESSO BAR KINGS X", None, "coffee"),
    ("UBER TRIP", "Transport > Taxi", "taxi"),
    ("ADDISON LEE CABS", None, "taxi"),
    ("BOLT RIDE", "Shopping > Online", "taxi"),
    ("PUREGYM", "Health > Fitness", "gym"),
    ("THE GYM GROUP", "Subscriptions > Memberships", "gym"),
    ("ANYTIME FITNESS", None, "gym"),
    ("BOOTS PHARMACY", "Health > Pharmacy", "pharmacy"),
    ("LLOYDS PHARMACY", "Shopping > Household", "pharmacy"),
    ("WELL CHEMIST", None, "pharmacy"),
    ("DELIVEROO", "Food & Drink > Takeaway", "takeaway"),
    ("JUST EAT", "Food & Drink > Eating out", "takeaway"),
    ("DOMINOS PIZZA", "Food & Drink > Takeaway", "takeaway"),
    ("KEBAB HOUSE", None, "takeaway"),
    ("SAINSBURYS", "Food & Drink > Groceries", "groceries"),
    ("ALDI STORES", "Food & Drink > Groceries", "groceries"),
    ("LIDL GB", "Shopping > Household", "groceries"),
    ("WAITROSE", "Food & Drink > Groceries", "groceries"),
    ("NCP CAR PARK", "Transport > Parking", "parking"),
    ("RINGGO PARKING", None, "parking"),
    ("ODEON CINEMA", "Entertainment > Events", "events"),
    ("VUE CINEMAS", "Shopping > Online", "events"),
    ("TURKISH BARBER", None, "hair"),
    ("TONI AND GUY SALON", "Personal care > Hair & beauty", "hair"),
    ("AMAZON MARKETPLACE", "Shopping > Online", "other"),
    ("NETFLIX", "Subscriptions > Streaming", "other"),
    ("SPOTIFY", "Subscriptions > Streaming", "other"),
    ("TRAINLINE", "Transport > Public transport", "other"),
    ("CURRYS", "Shopping > Electronics", "other"),
    ("IKEA", "Shopping > Household", "other"),
    ("EASYJET", "Travel > Flights", "other"),
    ("PREMIER INN", "Travel > Hotels", "other"),
    ("OCTOPUS ENERGY", "Bills > Energy", "other"),
    ("JOHN SMITH", None, "other"),  # a person
]
# the Category a concept is, as the resolver reads it
CONCEPTS: dict[str, str] = {
    "fuel": "Transport > Fuel",
    "coffee": "Food & Drink > Coffee",
    "taxi": "Transport > Taxi",
    "gym": "Health > Fitness",
    "pharmacy": "Health > Pharmacy",
    "takeaway": "Food & Drink > Takeaway",
    "groceries": "Food & Drink > Groceries",
    "parking": "Transport > Parking",
    "events": "Entertainment > Events",
    "hair": "Personal care > Hair & beauty",
}
# terms no resolver reads -> their concept, or None
TERMS: dict[str, str | None] = {
    "forecourt": "fuel",
    "cabs": "taxi",
    "minicab": "taxi",
    "chemist": "pharmacy",
    "workout": "gym",
    "espresso": "coffee",
    "latte": "coffee",
    "kebab": "takeaway",
    "pizza delivery": "takeaway",
    "supermarket": "groceries",
    "cinema": "events",
    "barber": "hair",
    "haircut": "hair",
    "credit score": None,
    "weather": None,
    "my pension": None,
    "lottery": None,
    "birthday present for mum": None,
}


def _cat(path: str | None) -> zero_shot.Category | None:
    if path is None:
        return None
    top, _, sub = path.partition(" > ")
    return (top, sub)


def record() -> None:
    e = embedding.OllamaEmbedder()
    cats = sorted(zero_shot.DESCRIPTIONS)
    names = [m[0] for m in MERCHANTS]
    terms = list(TERMS)
    vec = e.embed([zero_shot.DESCRIPTIONS[c] for c in cats] + names + terms)
    lab, mer, ter = vec[: len(cats)], vec[len(cats) : -len(terms)], vec[-len(terms) :]
    sim = embedding.similarity
    out = {
        "model": MODEL,
        "categories": [" > ".join(c) for c in cats],
        "merchants": {
            n: [sim(v, lv) for lv in lab] for n, v in zip(names, mer, strict=True)
        },
        "terms": {
            t: [sim(v, lv) for lv in lab] for t, v in zip(terms, ter, strict=True)
        },
        "term_merchants": {
            t: {n: sim(v, mv) for n, mv in zip(names, mer, strict=True)}
            for t, v in zip(terms, ter, strict=True)
        },
    }
    SIGNALS.write_text("# synthetic\n" + json.dumps(out, indent=1) + "\n")


def _signals() -> dict[str, Any]:
    raw = SIGNALS.read_text()
    return json.loads(raw.split("\n", 1)[1])


@functools.cache
def _fts_ranks(term: str) -> list[str]:
    """FTS5 BM25 over the names, with the app's own query builder."""
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE f USING fts5(body)")
    db.executemany(
        "INSERT INTO f(rowid, body) VALUES (?, ?)",
        list(enumerate(n for n, _, _ in MERCHANTS)),
    )
    q = search_index.fts_query(term)
    if q is None:
        return []
    rows = db.execute("SELECT rowid FROM f WHERE f MATCH ? ORDER BY rank, rowid", (q,))
    return [MERCHANTS[r[0]][0] for r in rows]


def _pr(found: set[Any], gold: set[Any]) -> tuple[float, float]:
    tp = len(found & gold)
    return (
        round(tp / len(found), 6) if found else 1.0,
        round(tp / len(gold), 6) if gold else 1.0,
    )


def score(sig: dict[str, Any], th: semantic.Thresholds) -> dict[str, float]:
    cats = [_cat(c) for c in sig["categories"]]
    cats_t: list[zero_shot.Category] = [c for c in cats if c is not None]
    found: set[tuple[str, str]] = set()
    gold: set[tuple[str, str]] = set()
    for concept, path in CONCEPTS.items():
        top, _, sub = path.partition(" > ")
        target = (top, sub)
        for name, filed, truth in MERCHANTS:
            if truth == concept and filed != path:
                gold.add((concept, name))
            if semantic.counts_for(
                sig["merchants"][name], cats_t, target, _cat(filed), th
            ):
                found.add((concept, name))
    fp, fr = _pr(found, gold)
    right = false_cat = nones = 0
    tfound: set[tuple[str, str]] = set()
    tgold: set[tuple[str, str]] = set()
    for t, concept in TERMS.items():
        got = semantic.term_category(sig["terms"][t], cats_t, th)
        want = _cat(CONCEPTS[concept]) if concept else None
        right += got == want
        if concept is None:
            nones += 1
            false_cat += got is not None
        sims = sig["term_merchants"][t]
        by_vec = sorted(sims, key=lambda n: (-sims[n], n))[: semantic.TOP]
        by_vec = [n for n in by_vec if sims[n] >= th.merchant]
        ids = {n: i for i, (n, _, _) in enumerate(MERCHANTS)}
        fused = semantic.rrf(
            [ids[n] for n in by_vec], [ids[n] for n in _fts_ranks(t)][: semantic.TOP]
        )
        tfound |= {(t, MERCHANTS[i][0]) for i in fused[: semantic.TOP]}
        tgold |= {(t, n) for n, _, c in MERCHANTS if concept and c == concept}
    mp, mr = _pr(tfound, tgold)
    return {
        "filed_precision": fp,
        "filed_recall": fr,
        "term_accuracy": round(right / len(TERMS), 6),
        "term_false_category": round(false_cat / nones, 6),
        "term_merchants_precision": mp,
        "term_merchants_recall": mr,
    }


def _middle(keyed: dict[Any, tuple[float, ...]]) -> Any:
    """The middle of the best-scoring settings, away from either edge of what
    works: the strictest overfits this small set, the loosest admits noise."""
    top = max(keyed.values())
    best = sorted(k for k, v in keyed.items() if v == top)
    return best[len(best) // 2]


def _best(sig: dict[str, Any]) -> semantic.Thresholds:
    """Per task, the best recall (then accuracy) with precision >= 0.9."""
    grid = range(200, 900, 10)
    margins = range(0, 200, 5)
    pairs = [(t, m) for t in grid for m in margins]

    def filed_key(t: int, m: int) -> tuple[float, ...]:
        s = score(sig, semantic.Thresholds(t, m, 1000, 0, 1000))
        ok = s["filed_precision"] >= MIN_PRECISION
        return (ok, s["filed_recall"] if ok else 0, s["filed_precision"])

    ft, fm = _middle({p: filed_key(*p) for p in pairs})

    def term_key(t: int, m: int) -> tuple[float, ...]:
        s = score(sig, semantic.Thresholds(1000, 0, t, m, 1000))
        return (s["term_false_category"] == 0, s["term_accuracy"])

    tt, tm = _middle({p: term_key(*p) for p in pairs})

    def merch_key(t: int) -> tuple[float, ...]:
        s = score(sig, semantic.Thresholds(1000, 0, 1000, 0, t))
        ok = s["term_merchants_precision"] >= MIN_PRECISION
        return (
            ok,
            s["term_merchants_recall"] if ok else 0,
            s["term_merchants_precision"],
        )

    mt = _middle({t: merch_key(t) for t in grid})
    return semantic.Thresholds(ft, fm, tt, tm, mt)


def evaluate() -> dict[str, Any]:
    sig = _signals()
    th = _best(sig)
    # the app's committed thresholds are the swept ones (a test checks)
    none = semantic.Thresholds(1000, 1000, 1000, 1000, 1000)
    return {
        "harness": "semantic_resolution",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(
            repr((MERCHANTS, CONCEPTS, TERMS)).encode() + SIGNALS.read_bytes()
        ).hexdigest(),
        "merchants": len(MERCHANTS),
        "terms": len(TERMS),
        "thresholds": {MODEL: th.__dict__},
        "models": {
            "names_only": {"metrics": score(sig, none)},
            MODEL: {"metrics": score(sig, th)},
        },
    }


def lower_is_better(name: str) -> bool:
    return name.endswith("false_category")


def main(argv: Sequence[str]) -> int:
    if "--record" in argv:
        record()
        return 0
    return gate.run("semantic_resolution", evaluate, RESULTS, lower_is_better, argv)


# the resolver must not read these terms already, or the task tests nothing
if any(rs.categories(t)[0] for t in TERMS):
    raise SystemExit("semantic_resolution: a term the resolver already reads")

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
