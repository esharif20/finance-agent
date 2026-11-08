"""Query planner evaluation (#271, docs/ML_EVALUATIONS.md): does a compound
spending question count the right Merchants and the right total?

    uv run python evals/query_planner.py                  # CI: results reproduce
    uv run python evals/query_planner.py --write          # refresh (see gate.py)
    uv run python evals/query_planner.py --model qwen3.5:9b   # local, opt-in

Metrics over `synthetic_query_plans` (gold Merchants: those of the gold rows
with spending in the range):

- `merchant_recall`, `merchant_precision`: means over cases of the counted
  Merchant set against the gold set.
- `figure_exact`: the total equals the gold total to the penny.
- `follow_up_accuracy`: follow-ups with the exact gold set and total.
- `sum_exact`: the total equals the sum of the rows listed plus `other`.
- `grounding`: the graph's reply, which quotes the total, passes the
  grounding check (path local_model).

Models: `planner` runs spend_for_query on the oracle's selectors through the
graph (FakeChatModel), so CI measures resolution and the Engine's sum.
`single_category` is the baseline: one spending_summary per month on the best
single top-level Category for the case (best F1 against the gold set; it
cannot follow up, so it answers each question afresh). `--model` asks a real
model for the selectors through the graph and records its scores and peak
memory in `results/query_planner_local.json`, which CI never reads.
"""

import json
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import keyring

import gate
import synthetic_query_plans as qp
from assistant_bench import GB, Peak, Recorder, _MemoryKeyring, sample_ollama  # pyright: ignore[reportPrivateUsage]
from finance_agent.assistant import (
    ChatModel,
    FakeChatModel,
    OllamaChatModel,
    OllamaError,
    Reply,
    ToolCall,
    graph,
    planner,
)
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.assistant.tools import registry
from finance_agent.assistant.tools.query import SpendForQueryOut
from finance_agent.assistant.tools.read import SpendingSummaryIn, spending_summary
from finance_agent.ledger import Ledger
from finance_agent.storage import open_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic_assistant import ME, seed  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "query_planner.json"
LOCAL_RESULTS = HERE / "results" / "query_planner_local.json"
TOOL = "spend_for_query"
type Factory = Callable[[qp.Case], ChatModel]


def pounds(minor: int) -> str:
    return f"£{minor / 100:,.2f}" if minor >= 0 else f"-£{-minor / 100:,.2f}"


def _turn(case: qp.Case) -> list[Reply]:
    """The oracle's script: route, one spend_for_query call, then a reply
    quoting the gold total (grounded only if the tool returned it)."""
    route = [] if graph.pre_route(case.question) else [Reply(content="ask")]
    return [
        *route,
        Reply(tool_calls=(ToolCall(name=TOOL, arguments=case.args),)),
        Reply(content=f"That came to {pounds(qp.gold_total(case))}."),
    ]


def oracle(case: qp.Case) -> ChatModel:
    first = _turn(qp.BY_ID[case.after]) if case.after else []
    return FakeChatModel([*first, *_turn(case)])


class _Seeded:
    def __init__(self, tmp: Path) -> None:
        self.engine = open_db(tmp / "db" / "query.db")
        rows = qp.rows()  # two statements: one export holds at most 150 rows
        seed(self.engine, ME, tmp, rows[:100])
        seed(self.engine, ME, tmp, rows[100:])
        with self.engine.connect() as conn:
            self.by_desc = {
                t.description: c.merchant_id
                for t in Ledger(conn, ME).transactions()
                if (c := t.categorisation)
            }

    def gold(self, case: qp.Case) -> set[int | None]:
        return {
            self.by_desc[desc]
            for d, desc, _ in qp.spending()
            if desc in case.gold and case.since <= f"{d:%Y-%m}" <= case.until
        }


def _f1(got: set[Any], want: set[Any]) -> float:
    hit = len(got & want)
    return 2 * hit / (len(got) + len(want)) if got or want else 1.0


def _scores(rows: list[dict[str, Any]]) -> dict[str, float]:
    def mean(k: str, rs: list[dict[str, Any]] = rows) -> float:
        return round(sum(r[k] for r in rs) / len(rs), 6) if rs else 1.0

    follow = [r for r in rows if r["follow_up"]]
    out = {
        "merchant_recall": mean("recall"),
        "merchant_precision": mean("precision"),
        "figure_exact": mean("exact"),
        "follow_up_accuracy": round(
            sum(r["exact"] and r["same_set"] for r in follow) / len(follow), 6
        ),
    }
    if "sum_exact" in rows[0]:
        out |= {"sum_exact": mean("sum_exact"), "grounding": mean("grounded")}
    return out


def _row(
    case: qp.Case, got: set[int | None], total: int, want: set[int | None]
) -> dict[str, Any]:
    hit = len(got & want)
    return {
        "id": case.id,
        "follow_up": case.after is not None,
        "recall": hit / len(want) if want else 1.0,
        "precision": hit / len(got) if got else float(not want),
        "exact": total == qp.gold_total(case),
        "same_set": got == want,
    }


def run_planner(s: _Seeded, factory: Factory) -> tuple[list[dict[str, Any]], list[str]]:
    """Each case through the graph; the counted set is read off the last
    spend_for_query result the model asked for, or the planner's own Engine
    read when code resolved the question with no model call (#317)."""
    rows: list[dict[str, Any]] = []
    outs: list[SpendForQueryOut] = []
    planned: list[SpendForQueryOut] = []
    real_run = registry.run
    real_spend = planner.spend

    def spy(*a: Any, **k: Any) -> Any:
        out = real_run(*a, **k)
        if isinstance(out, SpendForQueryOut):
            outs.append(out)
        return out

    def spend_spy(*a: Any, **k: Any) -> Any:
        out = real_spend(*a, **k)
        if not planned:  # the asked period: later reads are the one before
            planned.append(out)
            outs.append(out)
        return out

    for case in qp.CASES:
        rec = Recorder(factory(case))
        outs.clear()
        planned.clear()
        setattr(graph, "run", spy)  # noqa: B010 (the module global the graph calls)
        setattr(planner, "spend", spend_spy)  # noqa: B010
        with s.engine.connect() as conn:
            tx = conn.begin()
            try:
                thread = None
                if case.after:  # the conversation's earlier turn, unscored
                    first = qp.BY_ID[case.after].question
                    thread = ask(Assistant(rec, conn, qp.TODAY), first).thread_id
                    outs.clear()
                    planned.clear()
                a = ask(Assistant(rec, conn, qp.TODAY), case.question, thread)
            finally:
                tx.rollback()
                setattr(graph, "run", real_run)  # noqa: B010
                setattr(planner, "spend", real_spend)  # noqa: B010
        out = outs[-1] if outs else None
        got: set[int | None] = {r.merchant_id for r in out.merchants} if out else set()
        total = out.total.minor if out else 0
        row = _row(case, got, total, s.gold(case))
        row["sum_exact"] = bool(out) and (
            total == sum(r.amount.minor for r in out.merchants) + out.other.minor
        )
        row["grounded"] = (
            a.path in ("local_model", "planner") and pounds(total) in a.reply
        )
        rows.append(row)
    return rows, [r["id"] for r in rows if not (r["exact"] and r["same_set"])]


def run_baseline(s: _Seeded) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    with s.engine.connect() as conn:
        led = Ledger(conn, ME)
        for case in qp.CASES:
            span = [m for m in qp.MONTHS if case.since <= m <= case.until]
            want = s.gold(case)
            by_top: dict[str, set[int | None]] = {}
            for m in span:
                for top in led.spending_by_category(m):
                    by_top.setdefault(top.name, set()).update(
                        leaf.merchant_id
                        for sub in top.children
                        for leaf in sub.children
                    )
            best = max(sorted(by_top), key=lambda t: _f1(by_top[t], want))
            total = sum(
                c.amount.minor
                for m in span
                for c in spending_summary(led, SpendingSummaryIn(month=m)).categories
                if c.name == best
            )
            rows.append(_row(case, by_top[best], total, want))
    return rows, [r["id"] for r in rows if not (r["exact"] and r["same_set"])]


def _with_keyring[T](fn: Callable[[_Seeded], T]) -> T:
    prev = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())  # never the real Keychain
    try:
        with tempfile.TemporaryDirectory() as tmp:
            s = _Seeded(Path(tmp))
            try:
                return fn(s)
            finally:
                s.engine.dispose()
    finally:
        keyring.set_keyring(prev)


def _header() -> dict[str, Any]:
    return {
        "harness": "query_planner",
        "dataset": "synthetic_query_plans",
        "dataset_version": qp.DATASET_VERSION,
        "dataset_sha256": qp.fingerprint(),
        "cases": len(qp.CASES),
        "follow_up_cases": sum(c.after is not None for c in qp.CASES),
    }


def evaluate() -> dict[str, Any]:
    def both(s: _Seeded) -> dict[str, Any]:
        plan_rows, plan_fails = run_planner(s, oracle)
        base_rows, base_fails = run_baseline(s)
        return {
            "planner": {
                "model": "spend_for_query on the oracle's selectors",
                "model_version": "1",
                "metrics": _scores(plan_rows),
                "failures": plan_fails,
            },
            "single_category": {
                "model": "spending_summary, best single Category",
                "model_version": "1",
                "metrics": _scores(base_rows),
                "failures": base_fails,
            },
        }

    return _header() | {"models": _with_keyring(both)}


def local(model: str, path: Path = LOCAL_RESULTS) -> dict[str, Any]:
    """Score one Ollama model's own selectors; merge into `path`."""
    chat = OllamaChatModel(model)
    try:
        with Peak(lambda: sample_ollama(model)) as peak:
            rows, fails = _with_keyring(lambda s: run_planner(s, lambda _c: chat))
    finally:
        try:  # an empty chat with keep_alive 0 unloads the model
            OllamaChatModel(model, keep_alive="0").chat([])
        except (OllamaError, OSError):
            print(f"query planner eval: could not unload {model}", file=sys.stderr)
    entry = {
        "model": model,
        "model_version": peak.digest,
        "metrics": _scores(rows)
        | {"peak_memory_gb": round(max(peak.rss, peak.size) / GB, 3)},
        "failures": fails,
    }
    fresh = _header()
    old: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    same = old.get("dataset_sha256") == fresh["dataset_sha256"]
    fresh["models"] = (old["models"] if same else {}) | {model: entry}
    path.write_text(json.dumps(fresh, indent=2) + "\n")
    return fresh


def lower_is_better(name: str) -> bool:
    return name == "peak_memory_gb"


def main(argv: Sequence[str]) -> int:
    if "--model" in argv:
        i = list(argv).index("--model")
        if i + 1 >= len(argv):
            print("usage: query_planner.py --model <ollama model>", file=sys.stderr)
            return 2
        m = argv[i + 1]
        print(json.dumps(local(m)["models"][m]["metrics"], indent=2))
        return 0
    return gate.run("query_planner", evaluate, RESULTS, lower_is_better, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
