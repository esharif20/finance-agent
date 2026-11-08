"""The agreement gate's evaluation (#215 F, docs/ML_EVALUATIONS.md).

Merchant-level: what Corrections, rules and the UK base leave Uncategorised
on the synthetic categoriser set (v4, with its long tail) is scored by the
embedding kNN (nomic-embed-text), the local LLM with retrieval few-shot, and
the gate that files every guess in two tiers:

- confident (`auto`): the kNN alone at or above its threshold, or the kNN and
  the LLM agreeing at or above the agreement threshold (or the LLM alone,
  only if it clears 95% by itself). Calibrated here for >= 95% precision.
- low (`auto-low`): every other Merchant with any signal gets its best guess;
  its accuracy is reported as it is, because it is what the user sees.

    uv run python evals/categoriser_gate.py                 # CI: results reproduce
    uv run python evals/categoriser_gate.py --collect --llm qwen3.5:9b-q4_K_M [--write]
        # local only: nomic-embed-text and the LLM on Ollama, synthetic
        # descriptors only; waits until no other chat model is loaded and
        # unloads the LLM afterwards

`--collect` stores each Merchant's signals (kNN top score and Category, LLM
answer) in the results file; CI cannot run Ollama, so it recomputes every
metric from those committed signals plus the rules, and fails when a rule
change moves the residual set without a fresh local run.
"""

import hashlib
import json
import math
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import categoriser as cz
import gate
from finance_agent import categorise
from finance_agent.categorise import auto, embedding, llm
from metrics import per_category

HERE = Path(__file__).parent
CAP = auto.REVIEW_CAP
REAL_MERCHANTS = 575  # the user's first sync (#215, a count only)
type Category = tuple[str, str]
type Signals = dict[str, dict[str, Any]]
type Pick = Callable[[Mapping[str, Any]], tuple[Any, str] | None]


def results_path(model: str) -> Path:
    return (
        HERE
        / "results"
        / f"categoriser_gate_{re.sub(r'[^a-z0-9]+', '_', model.lower())}.json"
    )


def _merchant(r: cz.Row) -> str | None:
    return categorise.classify(r.descriptor, r.amount, "DEB", transfer=False).merchant


def residual(rows: Sequence[cz.Row]) -> dict[str, list[cz.Row]]:
    """Merchant -> its test rows that Corrections, rules and the base leave."""
    test = [r for r in rows if r.split == "test"]
    out: dict[str, list[cz.Row]] = {}
    for r, (p, _) in zip(test, cz.baseline(rows), strict=True):
        if p is None and (m := _merchant(r)):
            out.setdefault(m, []).append(r)
    return out


def _payee(
    m: str, rs: Sequence[cz.Row], shots: Sequence[tuple[str, Category]]
) -> llm.Payee:
    # the dataset has no dates: spread the rows 11 days apart from a Monday,
    # so the pattern reads "irregular, any day" and adds nothing it does not know
    start = date(2026, 1, 5)
    return llm.Payee(
        name=m,
        descriptions=tuple(dict.fromkeys(r.descriptor for r in rs)),
        payments=tuple(
            (start + timedelta(days=11 * i), Decimal(r.amount) / 100)
            for i, r in enumerate(rs)
        ),
        examples=tuple(shots),
    )


def collect(
    rows: Sequence[cz.Row],
    embedder: embedding.Embedder,
    classifier: llm.OllamaClassifier | None,
    cache: dict[str, Any] | None = None,
    budget: int | None = None,
) -> tuple[Signals, float]:
    """Every residual Merchant's signals, and the seconds the LLM took.
    `cache`: LLM answers by Merchant from earlier batches, filled as it goes
    (its "_seconds" adds up the time); `budget`: ask at most this many new
    ones (the rest get no answer: the run is incomplete)."""
    cache = {} if cache is None else cache
    known = cz.known(rows)
    todo = residual(rows)
    names = sorted(set(known) | set(todo))
    vec = dict(zip(names, embedder.embed(names), strict=True))
    examples = [(vec[n], n) for n in sorted(known)]
    out: Signals = {}
    spent = 0.0
    for m in sorted(todo):
        ranked = embedding.rank(
            vec[m], [(vec[n], {known[n]}) for n in sorted(known) if n != m]
        )
        near = embedding.neighbours(
            vec[m], [(v, n) for v, n in examples if n != m], llm.K
        )
        answer: str | None = None
        if classifier is not None and m in cache:
            answer = cache[m]
        elif classifier is not None and (budget is None or budget > 0):
            t = time.monotonic()
            got = classifier.classify(
                _payee(m, todo[m], [(n, known[n]) for _, n in near])
            )
            spent += time.monotonic() - t
            answer = cache[m] = llm.label(got) if got else llm.UNSURE
            budget = None if budget is None else budget - 1
        out[m] = {
            "gold": llm.label(cz.resolve(todo[m][0].category)),
            "rows": len(todo[m]),
            "knn": [ranked[0][0], llm.label(ranked[0][1])] if ranked else None,
            "llm": answer,
        }
    cache["_seconds"] = cache.get("_seconds", 0.0) + spent
    return out, cache["_seconds"]


# --- the gate, from signals ------------------------------------------------------


def _rows(signals: Signals, pick: "Pick") -> list[tuple[int, bool, str, str]]:
    """(score, correct, guess, gold) per residual *row* for Merchants `pick`
    gives a (score, guess) for."""
    out: list[tuple[int, bool, str, str]] = []
    for s in signals.values():
        got = pick(s)
        if got is not None:
            score, guess = got
            out += [(score, guess == s["gold"], guess, s["gold"])] * s["rows"]
    return out


def _llm(s: Mapping[str, Any]) -> str | None:
    a = s["llm"]
    return a if a and a != llm.UNSURE else None


def tiers(signals: Signals) -> dict[str, Any]:
    """Calibrate the confident tier, choose the low tier's best guess, and
    say which tier each Merchant lands in."""
    t_knn = cz.calibrate(
        [(sc, ok) for sc, ok, _, _ in _rows(signals, lambda s: s["knn"])]
    )

    def agree(s: Mapping[str, Any]) -> tuple[int, str] | None:
        k = s["knn"]
        return (k[0], k[1]) if k and k[0] < t_knn and _llm(s) == k[1] else None

    t_agree = cz.calibrate([(sc, ok) for sc, ok, _, _ in _rows(signals, agree)])
    alone = _rows(signals, lambda s: (llm.ANSWERED, a) if (a := _llm(s)) else None)
    llm_alone_ok = bool(alone) and sum(
        ok for _, ok, _, _ in alone
    ) * 100 >= cz.TARGET_PRECISION * len(alone)

    def confident(s: Mapping[str, Any]) -> tuple[str, str] | None:
        k, a = s["knn"], _llm(s)
        if k and k[0] >= t_knn:
            return "knn", k[1]
        if k and a == k[1] and k[0] >= t_agree:
            return "agree", k[1]
        if a and llm_alone_ok:
            return "llm", a
        return None

    rest = {m: s for m, s in signals.items() if confident(s) is None}
    hits = Counter[str]()
    for s in rest.values():
        k, a = s["knn"], _llm(s)
        if a and k and a != k[1]:  # a disagreement: which one to believe
            hits["llm"] += (a == s["gold"]) * s["rows"]
            hits["knn"] += (k[1] == s["gold"]) * s["rows"]
    prefer = "knn" if hits["knn"] > hits["llm"] else "llm"

    def low(s: Mapping[str, Any]) -> tuple[str, str] | None:
        k, a = s["knn"], _llm(s)
        if a and k and a == k[1]:
            return "agree", a
        if a and (prefer == "llm" or not k):
            return "llm", a
        if k:
            return "knn", k[1]
        return None

    kinds: dict[str, list[bool]] = {}
    for s in rest.values():
        if (g := low(s)) is not None:
            kinds.setdefault(g[0], []).extend([g[1] == s["gold"]] * s["rows"])
    confidence = {
        k: (sum(v) * 100 // len(v) if v else 0) for k, v in sorted(kinds.items())
    }
    return {
        "knn_threshold": t_knn,
        "agree_threshold": t_agree,
        "llm_alone": llm_alone_ok,
        "low_prefers": prefer,
        "low_confidence": confidence,  # percent right, per kind of low guess
        "confident": confident,
        "low": low,
    }


def _stage(
    test: Sequence[cz.Row], gold: Sequence[str], preds: Sequence[str | None]
) -> dict[str, float]:
    assigned = [(g, p) for g, p in zip(gold, preds, strict=True) if p is not None]
    right = sum(g == p for g, p in assigned)
    return {
        "precision": round(right / len(assigned), 6) if assigned else 0.0,
        "coverage": round(len(assigned) / len(gold), 6),
        "accuracy": round(right / len(gold), 6),
    }


def evaluate(
    rows: Sequence[cz.Row], signals: Signals, header: Mapping[str, Any]
) -> dict[str, Any]:
    test = [r for r in rows if r.split == "test"]
    gold = [llm.label(cz.resolve(r.category)) for r in test]
    base = [llm.label(p) if p else None for p, _ in cz.baseline(rows)]
    left = residual(rows)
    missing = sorted(set(left) - set(signals))
    sig = {m: signals[m] for m in sorted(left) if m in signals}
    t = tiers(sig)
    merchants = [_merchant(r) for r in test]

    def fill(pick: Pick) -> list[str | None]:
        out: list[str | None] = []
        for p, m in zip(base, merchants, strict=True):
            got = pick(sig[m]) if p is None and m in sig else None
            out.append(p if p is not None else (got[1] if got else None))
        return out

    knn = fill(
        lambda s: (
            (0, s["knn"][1]) if s["knn"] and s["knn"][0] >= t["knn_threshold"] else None
        )
    )
    alone = fill(lambda s: (0, a) if (a := _llm(s)) else None)
    confident = fill(t["confident"])
    filed = fill(lambda s: t["confident"](s) or t["low"](s))
    lowonly = [
        (g, f)
        for g, c, f in zip(gold, confident, filed, strict=True)
        if c is None and f is not None
    ]
    no_signal = [
        m for m, s in sig.items() if t["confident"](s) is None and t["low"](s) is None
    ]
    low_merchants = [
        m
        for m, s in sig.items()
        if t["confident"](s) is None and t["low"](s) is not None
    ]
    share = len(no_signal) / len(sig) if sig else 0.0
    stages = {
        "rules": _stage(test, gold, base),
        "rules+knn": _stage(test, gold, knn),
        "rules+llm": _stage(test, gold, alone),
        "gate_confident": _stage(test, gold, confident),
        "gate_all_tiers": _stage(test, gold, filed),
    }
    per_sub = {
        f"{name}.{label}.precision": round(p, 6)
        for name, preds in (("confident", confident), ("all_tiers", filed))
        for label, (p, _) in sorted(per_category(gold, preds).items())
        if any(x == label for x in preds)
    }
    guessed = Counter(
        f for c, f in zip(confident, filed, strict=True) if c is None and f
    )
    groceries = "Food & Drink › Groceries"
    by_case: dict[str, list[bool]] = {}
    for r, g, f in zip(test, gold, filed, strict=True):
        by_case.setdefault(r.case, []).append(g == f)
    return {
        **header,
        "harness": "categoriser_gate",
        "dataset": cz.DATASET.name,
        "dataset_version": cz.DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(cz.DATASET.read_bytes()).hexdigest(),
        "rules_version": cz.rules_version(),
        "prompt_version": llm.PROMPT_VERSION,
        "residual_merchants": len(sig),
        "missing_signals": missing,
        "calibration": {k: v for k, v in t.items() if k not in ("confident", "low")},
        "review": {
            "cap": CAP,
            "low_tier_merchants": len(low_merchants),
            "no_signal_merchants": len(no_signal),
            # a sync of about 4,000 rows from 575 Merchants, all of them left by
            # the rules (the worst case): the cap plus those with no signal
            "items_per_sync_575": min(CAP, round((1 - share) * REAL_MERCHANTS))
            + math.ceil(share * REAL_MERCHANTS),
        },
        "low_tier_accuracy": round(sum(g == f for g, f in lowonly) / len(lowonly), 6)
        if lowonly
        else 0.0,
        "groceries_share": {
            "gold": round(
                sum(s["gold"] == groceries for s in sig.values()) / len(sig), 6
            )
            if sig
            else 0.0,
            "low_guesses": round(guessed[groceries] / sum(guessed.values()), 6)
            if guessed
            else 0.0,
        },
        "models": {
            "gate": {
                "model": "rules+knn+llm agreement gate",
                "model_version": header.get("model_version", ""),
                "metrics": {
                    **{f"{s}.{k}": v for s, m in stages.items() for k, v in m.items()},
                    **per_sub,
                },
                "by_case": {
                    c: {"rows": len(v), "accuracy": round(sum(v) / len(v), 6)}
                    for c, v in sorted(by_case.items())
                },
            }
        },
        "signals": sig,
    }


def lower_is_better(name: str) -> bool:
    return False


# --- Ollama coordination (local runs only) -----------------------------------------


def _loaded(c: llm.OllamaClassifier) -> list[str]:
    reply = c._call("GET", "/api/ps")  # pyright: ignore[reportPrivateUsage]
    models: Iterable[Any] = reply.get("models", []) if isinstance(reply, dict) else []  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return [str(m.get("name")) for m in models if isinstance(m, dict)]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportUnknownArgumentType]


def wait_for_idle(c: llm.OllamaClassifier, minutes: int = 60) -> None:
    """Another agent benchmarks the Assistant on the same 16 GB Mac: start
    only once `ollama ps` shows nothing loaded."""
    for _ in range(minutes * 4):
        others = _loaded(c)
        if not others:
            return
        print(f"waiting: {', '.join(others)} loaded", file=sys.stderr)
        time.sleep(15)
    raise SystemExit("another model stayed loaded; try later")


def unload(c: llm.OllamaClassifier) -> None:
    c._call("POST", "/api/generate", {"model": c.model, "keep_alive": 0})  # pyright: ignore[reportPrivateUsage]


def main(argv: Sequence[str]) -> int:
    if "--collect" in argv:
        model = argv[argv.index("--llm") + 1] if "--llm" in argv else llm.MODEL
        emb = embedding.OllamaEmbedder(keep_alive="0s")  # one model at a time
        clf = llm.OllamaClassifier(model)
        e_version, l_version = embedding.stored_model(emb), llm.stored_model(clf)
        if e_version is None or l_version is None:
            print(
                "categoriser gate: nomic-embed-text or the LLM is not on Ollama",
                file=sys.stderr,
            )
            return 1
        # small batches, resumable: answers so far live in a scratch cache
        cache_path = (
            Path(argv[argv.index("--cache") + 1]) if "--cache" in argv else None
        )
        batch = int(argv[argv.index("--batch") + 1]) if "--batch" in argv else None
        cache: dict[str, Any] = (
            json.loads(cache_path.read_text())
            if cache_path and cache_path.exists()
            else {}
        )
        if cache.get("_llm") not in (None, l_version):
            print("categoriser gate: the cache is another model's", file=sys.stderr)
            return 1
        cache["_llm"] = l_version
        wait_for_idle(clf)
        rows = cz.load()
        try:
            signals, spent = collect(rows, emb, clf, cache, batch)
        finally:
            unload(clf)
            if cache_path:
                cache_path.write_text(json.dumps(cache, indent=1))
        n = len(signals)
        left = sum(1 for v in signals.values() if v["llm"] is None)
        if left:
            print(
                f"categoriser gate: {left} merchants left; run again", file=sys.stderr
            )
            return 0
        print(
            f"categoriser gate: {n} merchants in {spent:.0f}s"
            f" ({spent / max(n, 1):.2f}s each; {REAL_MERCHANTS} would take"
            f" {spent / max(n, 1) * REAL_MERCHANTS / 60:.1f} min)",
            file=sys.stderr,
        )
        header = {
            "model_version": f"{e_version}+{l_version.removeprefix(llm.PREFIX)}",
            "embedder": e_version,
            "llm": l_version,
        }
        return gate.run(
            "categoriser_gate", lambda: evaluate(rows, signals, header),
            results_path(model),
            lower_is_better, argv,
        )  # fmt: skip
    status = 0
    for path in sorted((HERE / "results").glob("categoriser_gate_*.json")):
        committed = json.loads(path.read_text())
        header = {k: committed[k] for k in ("model_version", "embedder", "llm")}
        status |= gate.run(
            "categoriser_gate",
            lambda c=committed, h=header: evaluate(cz.load(), c["signals"], h),
            path, lower_is_better, argv,
        )  # fmt: skip
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
