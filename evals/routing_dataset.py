"""The model router's labelled dataset (#283, #251 decision 12, RouteLLM /
FrugalGPT): synthetic questions labelled "4b" (qwen3.5 4b is sufficient) or
"9b" (it needs the 9b), the set #284's router trains and picks its threshold on.

    uv run python evals/routing_dataset.py             # CI: the set is sound
    uv run python evals/routing_dataset.py --build     # the unlabelled set
    uv run python evals/routing_dataset.py --write     # results (see gate.py)
    uv run python evals/routing_dataset.py --generate  # local: paraphrase, label

CI checks the committed set's schema, `# synthetic` marker and version, and
that its results reproduce; only `--generate` talks to (localhost) Ollama.

**Seeds and templates.** Every golden case (`assistant_golden.py`) seeds rows
that keep its expected route, tool calls, figures and refusal, so the existing
checks score them: the case itself, two style templates picked by SEED, and up
to PARAPHRASES paraphrases the 9b writes offline (filtered: no digits outside
the case's own, no instruction-like text, names and times kept). A case's rows
share its split, so no paraphrase of a test question is trained on.

**Labels.** `--generate` loads one model at a time (9b paraphrases; then the 4b
labels, then the 9b labels, each over two seeded runs) and unloads each with
keep_alive 0, even on failure, sampling peak memory. A row passes a run when
every check `assistant_bench` applies passes. Label "9b" when the 9b's pass
rate over the runs beats the 4b's; otherwise "4b", including when both fail
(`both_fail`: the 9b would not help). Label noise is the agreement of the two
runs' labels.

**Real-style traffic.** The golden set over-samples hard turns (refusals,
writes, plans), so each row carries a weight: its stratum's share of real
traffic (TRAFFIC, fixed before any label existed and in the fingerprint)
split evenly over the stratum's rows. Weighted metrics estimate real use;
unweighted ones are reported too. `share_4b_weighted` is an oracle router's 4b
share at no quality loss: the ceiling for #284's 80-90% band.

Features are read from the question text only (what a router sees before the
turn): the semantic-layer dimensions of #271 it mentions, the metric words,
the pre-router's verdict, length and whether it follows up. Synthetic only."""

import hashlib
import json
import random
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import assistant_bench as bench
import assistant_golden as golden
import gate
from finance_agent.assistant import (
    ChatModel,
    Message,
    OllamaChatModel,
    OllamaError,
    graph,
)
from finance_agent.assistant.ollama import MODELS
from finance_agent.assistant.tools.query import DIMENSIONS
from finance_agent.ledger.taxonomy import TAXONOMY

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic_assistant import DECEMBER, ROWS  # noqa: E402

DATASET_VERSION = 1
SEED = 283
HERE = Path(__file__).parent
DATASET = HERE / "datasets" / "routing_v1.jsonl"
RESULTS = HERE / "results" / "routing_labels.json"
PARTIAL = HERE / "results" / "routing_partial.json"  # a crashed run resumes

SMALL, LARGE = "4b", "9b"
NAMES = {SMALL: MODELS["fast"], LARGE: MODELS["careful"]}
RUN_SEEDS = (1, 2)
PARAPHRASES = 3  # kept per case, of ASKED asked for
ASKED = 5
MAX_ERRORS = 0.2  # a run with more model errors than this labels nothing
HEADER = "# synthetic"

# Each stratum's share of real turns: an assumption (most turns look up one
# figure), fixed before labelling. Change it only with a DATASET_VERSION bump.
TRAFFIC = {
    "lookup": 0.60,
    "follow_up": 0.10,
    "compound": 0.08,
    "plan": 0.08,
    "memory": 0.06,
    "correct": 0.05,
    "refusal": 0.03,
}
STRATA = tuple(TRAFFIC)
SPLITS = ("train", "test")
ROW_KEYS = {
    "id",
    "source",
    "variant",
    "stratum",
    "split",
    "weight",
    "question",
    "after",
    "features",
    "scores",
    "label",
    "both_fail",
}
FEATURE_KEYS = {"words", "follow_up", "pre_route", "money", "dimensions", "metrics"}

# --- templates --------------------------------------------------------------


def _lcfirst(q: str) -> str:
    first = q.split(" ", 1)[0]
    return q if first == "I" or first.startswith("I'") else q[:1].lower() + q[1:]


STYLES: dict[str, Callable[[str], str]] = {
    "lower": lambda q: q.lower().rstrip("?.!"),  # typed on a phone
    "hey": lambda q: "Hey, " + _lcfirst(q),
    "thanks": lambda q: q + " Thanks.",
    "quick": lambda q: "Quick question. " + q,
}


def stratum(case: golden.Case) -> str:
    if case.refusal:
        return "refusal"
    if case.after:
        return "follow_up"
    if case.route != "ask":
        return case.route
    if any(c.name == "spend_for_query" for c in case.calls):
        return "compound"
    return "lookup"


# --- features: the question text only -----------------------------------------

_MONTHS = (
    "january february march april may june july august september october"
    " november december"
)
_TIME = re.compile(
    rf"\b({'|'.join(_MONTHS.split())}|20\d\d|today|tonight|week|month|months"
    r"|year|summer|latest|recent|usual|usually|lately|regularly)\b",
    re.I,
)
_CATEGORY_NAMES = sorted(
    {n.casefold() for top, subs in TAXONOMY.items() for n in (top, *subs)},
    key=len,
    reverse=True,
)
_CATEGORY = re.compile(
    r"\b(" + "|".join(re.escape(n) for n in _CATEGORY_NAMES) + r")\b", re.I
)
# the synthetic Ledger's Merchant words; #284 reads the Ledger's own names
_MERCHANT_WORDS = sorted(
    {d.split()[0].casefold() for _, _, d, _ in (*ROWS, *DECEMBER)} - {"acme"}
)
_MERCHANT = re.compile(r"\b(" + "|".join(_MERCHANT_WORDS) + r")\b", re.I)
_ACCOUNT = re.compile(r"\b(account|accounts|card|cards)\b", re.I)
_METRICS = {
    "spend": re.compile(r"\b(spend|spent|spending|cost|costs|paid|pay|paying)\b", re.I),
    "count": re.compile(r"\b(how many|number of|how often)\b", re.I),
    "merchants": re.compile(r"\b(where|which (?:shops?|merchants?|stores?))\b", re.I),
}
_MONEY = re.compile(r"£|\d")


def features(question: str, follow_up: bool) -> dict[str, Any]:
    dims = {
        "time_range": _TIME,
        "category": _CATEGORY,
        "merchant": _MERCHANT,
        "account": _ACCOUNT,
    }
    # the semantic layer's names (#271); keyword needs the planner, not text
    found = sorted(
        d for d, rx in dims.items() if d in DIMENSIONS and rx.search(question)
    )
    return {
        "words": len(question.split()),
        "follow_up": follow_up,
        "pre_route": graph.pre_route(question),
        "money": bool(_MONEY.search(question)),
        "dimensions": found,
        "metrics": sorted(m for m, rx in _METRICS.items() if rx.search(question)),
    }


# --- paraphrase filter --------------------------------------------------------

_DIGITS = re.compile(r"\d+(?:[.,]\d+)*")
_INSTRUCTION = re.compile(
    r"\b(ignore|disregard|instructions?|system|prompt|assistant|rules?|override"
    r"|jailbreak|pretend|as an ai|you are|tool|function|json)\b"
    r"|[<>{}`\[\]]|https?:|www\.",
    re.I,
)
_TIME_PHRASES = re.compile(
    r"\b(this month|last month|today|this week|this year|next three months"
    r"|last two months|latest|by the summer|this summer)\b",
    re.I,
)
_BULLET = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")


def _strings(v: Any) -> Iterable[str]:
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:  # pyright: ignore[reportUnknownVariableType]
            yield from _strings(x)


def _kept_terms(case: golden.Case) -> set[str]:
    """What a paraphrase must keep for the case's checks still to hold."""
    q = case.question.casefold()
    terms = {
        s.casefold()
        for c in case.calls
        for s in _strings(c.args)
        if not s.startswith(golden.TXN) and s.casefold() in q
    }
    terms |= {m.casefold() for m in re.findall(_TIME, q) if m.isalpha()}
    terms |= {m.casefold() for m in _TIME_PHRASES.findall(q)}
    return terms | {m.casefold() for m in _MERCHANT.findall(q)}


def _norm(q: str) -> str:
    return re.sub(r"[^a-z0-9£ ]", "", q.casefold()).strip()


def _instructions(text: str) -> set[str]:
    return {m.group(0).casefold() for m in _INSTRUCTION.finditer(text)}


def acceptable(case: golden.Case, text: str) -> bool:
    """A paraphrase may join the set: one line, its digits exactly the case's
    (no new figure, none dropped), no instruction-like text the case lacks, and
    every name and time the checks depend on kept."""
    if "\n" in text or not 3 <= len(text.split()) <= 40:
        return False
    if Counter(_DIGITS.findall(text)) != Counter(_DIGITS.findall(case.question)):
        return False
    if _instructions(text) - _instructions(case.question):
        return False
    low = text.casefold()
    return all(re.search(rf"\b{re.escape(t)}\b", low) for t in _kept_terms(case))


PARAPHRASE_SYSTEM = (
    "You rewrite messages a person types into their personal finance app. Write"
    f" {ASKED} different ways the same person might say the same thing. Keep its"
    " meaning, and keep every name, month, date and amount exactly as written."
    " Reply with one rewrite per line and nothing else."
)


def paraphrase(chat: ChatModel, case: golden.Case) -> list[str]:
    reply = chat.chat(
        [
            Message(role="system", content=PARAPHRASE_SYSTEM),
            Message(role="user", content=case.question),
        ]
    ).content
    seen = {_norm(case.question)}
    out: list[str] = []
    for line in reply.splitlines():
        text = _BULLET.sub("", line).strip().strip('"')
        if text and _norm(text) not in seen and acceptable(case, text):
            seen.add(_norm(text))
            out.append(text)
    return out[:PARAPHRASES]


# --- rows ---------------------------------------------------------------------


def _splits() -> dict[str, str]:
    """A case and all its rows are test when it is every 4th of its stratum
    (sorted by id, from the 2nd), so every stratum with 2+ cases has a test."""
    out: dict[str, str] = {}
    for s in STRATA:
        ids = sorted(c.id for c in golden.CASES if stratum(c) == s)
        out |= {cid: SPLITS[i % 4 == 1] for i, cid in enumerate(ids)}
    return out


def _row(case: golden.Case, variant: str, question: str, split: str) -> dict[str, Any]:
    return {
        "id": f"{case.id}~{variant}",
        "source": case.id,
        "variant": variant,
        "stratum": stratum(case),
        "split": split,
        "weight": 0.0,
        "question": question,
        "after": case.after,
        "features": features(question, case.after is not None),
        "scores": None,
        "label": None,
        "both_fail": None,
    }


def build(paraphrases: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """The unlabelled rows: each case, two styles and its paraphrases."""
    splits = _splits()
    rows: list[dict[str, Any]] = []
    for case in golden.CASES:
        split = splits[case.id]
        rows.append(_row(case, "original", case.question, split))
        pick = random.Random(f"{SEED}:{case.id}")  # noqa: S311 - not secret
        styles = pick.sample(sorted(STYLES), 2)
        rows += [
            _row(case, f"style:{s}", STYLES[s](case.question), split) for s in styles
        ]
        for i, p in enumerate((paraphrases or {}).get(case.id, ())):
            rows.append(_row(case, f"paraphrase:{i}", p, split))
    per = Counter(r["stratum"] for r in rows)
    for r in rows:
        r["weight"] = round(TRAFFIC[r["stratum"]] / per[r["stratum"]], 9)
    return rows


def case_of(row: dict[str, Any]) -> golden.Case:
    """The golden case with the row's question: same checks, new wording."""
    return bench.BY_ID[row["source"]]._replace(id=row["id"], question=row["question"])


# --- the file -----------------------------------------------------------------


def write(path: Path, header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    lines = [HEADER, f"# routing dataset v{DATASET_VERSION}, seed {SEED}"]
    lines += [json.dumps(header, sort_keys=True)]
    lines += [json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows]
    path.write_text("\n".join(lines) + "\n")


def load(path: Path | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lines = (path or DATASET).read_text().splitlines()
    data = [json.loads(line) for line in lines if line and not line.startswith("#")]
    return data[0], data[1:]


def fingerprint(rows: list[dict[str, Any]]) -> str:
    """The dataset proper: questions, features, strata, weights and splits; the
    scores and labels are results."""
    keep = ROW_KEYS - {"scores", "label", "both_fail"}
    body = [{k: r[k] for k in sorted(keep)} for r in rows]
    blob = json.dumps([TRAFFIC, SEED, body], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def validate(path: Path | None = None) -> list[str]:
    """What is wrong with the committed file; [] when it is sound."""
    path = path or DATASET
    if not path.read_text().startswith(HEADER + "\n"):
        return [f"{path.name}: must start with '{HEADER}'"]
    header, rows = load(path)
    problems: list[str] = []
    if header.get("dataset_version") != DATASET_VERSION:
        problems.append(f"dataset_version {header.get('dataset_version')}")
    status = header.get("labels")
    if status not in ("pending", "labelled"):
        problems.append(f"labels status {status!r}")
    if not rows or header.get("fingerprint") != fingerprint(rows):
        problems.append("no rows, or the fingerprint does not match them")
    ids = [r.get("id") for r in rows]
    if len(set(ids)) != len(ids):
        problems.append("duplicate row ids")
    if abs(sum(r.get("weight", 0) for r in rows) - 1) > 1e-6:
        problems.append("weights do not sum to 1")
    splits = _splits()
    for r in rows:
        at = f"row {r.get('id')}"
        if set(r) != ROW_KEYS:
            problems.append(f"{at}: keys {sorted(set(r) ^ ROW_KEYS)}")
            continue
        case = bench.BY_ID.get(r["source"])
        if case is None:
            problems.append(f"{at}: unknown source")
            continue
        if (r["stratum"], r["split"], r["after"]) != (
            stratum(case),
            splits[case.id],
            case.after,
        ):
            problems.append(f"{at}: stratum, split or follow-up is not its case's")
        if not isinstance(r["question"], str) or not r["question"].strip():
            problems.append(f"{at}: empty question")
            continue
        if r["features"] != features(r["question"], case.after is not None):
            problems.append(f"{at}: features are not the question's")
        if r["variant"].startswith("paraphrase:") and not acceptable(
            case, r["question"]
        ):
            problems.append(f"{at}: paraphrase fails the filter")
        elif not r["variant"].startswith("paraphrase:") and r["question"] != (
            case.question
            if r["variant"] == "original"
            else STYLES[r["variant"].removeprefix("style:")](case.question)
        ):
            problems.append(f"{at}: question is not its template's")
        labelled = r["label"] is not None
        if labelled != (status == "labelled") or r["label"] not in (None, SMALL, LARGE):
            problems.append(f"{at}: label {r['label']!r} with status {status}")
        if labelled and set(r["scores"] or {}) != {SMALL, LARGE}:
            problems.append(f"{at}: scores for both models missing")
    return problems


# --- labels -------------------------------------------------------------------

type Runner = Callable[[int], bench.Factory]  # a run's seed -> the model factory
type Checks = dict[str, bool]


def checks(case: golden.Case, fails: dict[str, list[str]]) -> Checks:
    """The checks `assistant_bench` applied to `case`, by whether each passed."""
    keys = ["route", "tool_calls", "printed", "errors"]
    keys += ["refusal", "refusal_no_proposal"] if case.refusal else ["grounding"]
    return {k: case.id not in fails.get(k, ()) for k in keys}


def score(
    rows: list[dict[str, Any]], runner: Runner
) -> tuple[list[list[Checks]], float]:
    """Each row's checks per seeded run, and the p50 latency (ms) of a timed run."""
    cases = tuple(case_of(r) for r in rows)
    per_row: list[list[Checks]] = [[] for _ in rows]
    p50: list[float] = []
    for seed in RUN_SEEDS:
        out = bench.evaluate({"m": ("", "", runner(seed))}, timed=True, cases=cases)
        m = out["models"]["m"]
        if m["metrics"]["model_error_rate"] > MAX_ERRORS:  # Ollama down: no labels
            raise RuntimeError(
                f"model errors on {m['metrics']['model_error_rate']:.0%}"
            )
        p50.append(m["metrics"]["latency_p50_ms"])
        for i, c in enumerate(cases):
            per_row[i].append(checks(c, m["failures"]))
    return per_row, round(sum(p50) / len(p50), 1)


def ok(run: Checks) -> bool:
    return all(run.values())


def label(rows: list[dict[str, Any]], scores: dict[str, list[list[Checks]]]) -> None:
    for i, r in enumerate(rows):
        r["scores"] = {k: scores[k][i] for k in (SMALL, LARGE)}
        p = {k: sum(map(ok, v)) / len(v) for k, v in r["scores"].items()}
        r["label"] = LARGE if p[LARGE] > p[SMALL] else SMALL
        r["both_fail"] = p[SMALL] == 0 and p[LARGE] == 0


def _run_label(r: dict[str, Any], run: int) -> str:
    small, large = (ok(r["scores"][k][run]) for k in (SMALL, LARGE))
    return LARGE if large and not small else SMALL


# --- results (what CI reproduces from the committed set) ------------------------


def _wmean(rows: list[dict[str, Any]], x: Callable[[dict[str, Any]], bool]) -> float:
    w = sum(r["weight"] for r in rows)
    return round(sum(r["weight"] * x(r) for r in rows) / w, 6) if w else 0.0


def _mean(rows: list[dict[str, Any]], x: Callable[[dict[str, Any]], bool]) -> float:
    return round(sum(map(x, rows)) / len(rows), 6) if rows else 0.0


def golden_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    """`assistant_bench`'s metrics on the original questions, first run: they
    must equal the bench's own on the same model (the drift check)."""
    orig = [r for r in rows if r["variant"] == "original"]
    refusals = [r for r in orig if bench.BY_ID[r["source"]].refusal]
    answers = [r for r in orig if not bench.BY_ID[r["source"]].refusal]

    def ok(of: list[dict[str, Any]], check: str) -> float:
        return _mean(of, lambda r: r["scores"][key][0][check])

    return {
        "golden_route_accuracy": ok(orig, "route"),
        "golden_tool_call_accuracy": ok(orig, "tool_calls"),
        "golden_grounding_pass_rate": ok(answers, "grounding"),
        "golden_refusal_correctness": ok(refusals, "refusal"),
        "golden_refusal_no_proposal": ok(refusals, "refusal_no_proposal"),
    }


def evaluate(path: Path | None = None) -> dict[str, Any]:
    header, rows = load(path)
    out: dict[str, Any] = {
        "harness": "routing",
        "dataset": "routing_v1",
        "dataset_version": header["dataset_version"],
        "dataset_sha256": fingerprint(rows),
        "rows": len(rows),
        "cases": len({r["source"] for r in rows}),
        "rows_by_stratum": dict(sorted(Counter(r["stratum"] for r in rows).items())),
        "rows_by_split": dict(sorted(Counter(r["split"] for r in rows).items())),
        "traffic": TRAFFIC,
        "labels": {"status": header["labels"]},
        "models": {},
    }
    if header["labels"] != "labelled":
        return out

    def small(r: dict[str, Any]) -> bool:
        return r["label"] == SMALL

    out["labels"] |= {
        "share_4b": _mean(rows, small),
        "share_4b_weighted": _wmean(rows, small),
        "share_4b_weighted_test": _wmean(
            [r for r in rows if r["split"] == "test"], small
        ),
        "share_4b_by_stratum": {
            s: _mean([r for r in rows if r["stratum"] == s], small) for s in STRATA
        },
        "both_fail_rate": _mean(rows, lambda r: r["both_fail"]),
        "label_agreement": _mean(rows, lambda r: _run_label(r, 0) == _run_label(r, 1)),
        "oracle_pass_rate_weighted": _wmean(
            rows, lambda r: any(ok(r["scores"][k][0]) for k in (SMALL, LARGE))
        ),
        "paraphrases": sum(r["variant"].startswith("paraphrase:") for r in rows),
    }
    for key in (SMALL, LARGE):
        meta = header["models"][key]

        def passed(r: dict[str, Any], k: str = key) -> bool:
            return ok(r["scores"][k][0])

        out["models"][key] = {
            "model": meta["model"],
            "model_version": meta["model_version"],
            "metrics": {
                "pass_rate": _mean(rows, passed),  # always-this-model quality
                "pass_rate_weighted": _wmean(rows, passed),
                **golden_metrics(rows, key),
                "latency_p50_ms": meta["latency_p50_ms"],
                "peak_memory_gb": meta["peak_memory_gb"],
            },
        }
    return out


def lower_is_better(name: str) -> bool:
    return name.endswith(("_ms", "_gb"))


# --- the local run ---------------------------------------------------------------


def _unload(name: str) -> None:
    try:  # an empty chat with keep_alive 0 unloads the model
        OllamaChatModel(name, keep_alive="0").chat([])
    except OllamaError:
        print(f"routing: could not unload {name}", file=sys.stderr)


class Local:
    """How `generate` reaches models: localhost Ollama, or fakes in tests."""

    def chat(self, name: str, seed: int | None) -> ChatModel:
        return OllamaChatModel(name, seed=seed)

    def runner(self, name: str) -> Runner:
        def for_seed(seed: int) -> bench.Factory:
            chat = self.chat(name, seed)
            return lambda _case, _ids: chat

        return for_seed

    def sample(self, name: str) -> tuple[int, int, str]:
        return bench.sample_ollama(name)

    def unload(self, name: str) -> None:
        _unload(name)


def _phase[T](io: Local, name: str, work: Callable[[], T]) -> tuple[T, dict[str, Any]]:
    """Run `work` with `name` loaded alone; unload it whatever happens."""
    try:
        with bench.Peak(lambda: io.sample(name)) as peak:
            io.chat(name, None).chat([])  # load, so the peak covers it all
            got = work()
    finally:
        io.unload(name)
    meta = {
        "model": name,
        "model_version": peak.digest,
        "peak_memory_gb": round(max(peak.rss, peak.size) / bench.GB, 3),
    }
    return got, meta


def generate(io: Local | None = None, partial: Path = PARTIAL) -> int:
    """Paraphrase with the 9b, then label with the 4b and the 9b, one model
    loaded at a time; finished phases are kept in `partial` so a crash resumes."""
    io = io or Local()
    state: dict[str, Any] = json.loads(partial.read_text()) if partial.exists() else {}

    def save() -> None:
        partial.write_text(json.dumps(state))

    if "paraphrases" not in state:
        writer = io.chat(NAMES[LARGE], 0)
        got, meta = _phase(
            io,
            NAMES[LARGE],
            lambda: {c.id: paraphrase(writer, c) for c in golden.CASES},
        )
        state["paraphrases"], state["paraphraser"] = got, meta
        save()
    rows = build(state["paraphrases"])
    for key in (SMALL, LARGE):  # the 4b, then the 9b
        if key not in state:
            (scores, p50), meta = _phase(
                io, NAMES[key], lambda k=key: score(rows, io.runner(NAMES[k]))
            )
            state[key] = {"scores": scores, "meta": meta | {"latency_p50_ms": p50}}
            save()
    label(rows, {k: state[k]["scores"] for k in (SMALL, LARGE)})
    header = {
        "dataset_version": DATASET_VERSION,
        "fingerprint": fingerprint(rows),
        "labels": "labelled",
        "runs": list(RUN_SEEDS),
        "paraphraser": state["paraphraser"],
        "models": {k: state[k]["meta"] for k in (SMALL, LARGE)},
    }
    new = DATASET.with_suffix(".jsonl.new")
    write(new, header, rows)
    fresh = json.loads(json.dumps(evaluate(new)))
    committed = json.loads(RESULTS.read_text()) if RESULTS.exists() else None
    if committed and committed["labels"]["status"] == "labelled":
        problems = gate.lowered(fresh, committed, lower_is_better)
        if problems:
            for p in problems:
                print(f"routing: {p}", file=sys.stderr)
            print(f"routing: not committed; the new set is in {new}", file=sys.stderr)
            return 1
    new.replace(DATASET)
    RESULTS.write_text(json.dumps(fresh, indent=2) + "\n")
    partial.unlink(missing_ok=True)
    print(json.dumps(fresh["labels"], indent=2))
    return 0


def main(argv: Sequence[str]) -> int:
    if "--generate" in argv:
        return generate()
    if "--build" in argv:
        rows = build()
        header = {
            "dataset_version": DATASET_VERSION,
            "fingerprint": fingerprint(rows),
            "labels": "pending",
        }
        write(DATASET, header, rows)
    if problems := validate():
        for p in problems[:20]:
            print(f"routing eval: {p}", file=sys.stderr)
        return 1
    return gate.run("routing", evaluate, RESULTS, lower_is_better, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
