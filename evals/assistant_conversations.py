"""Conversation bench (#317, docs/ML_EVALUATIONS.md "Assistant quality"):
scripted 5-turn conversations over the synthetic Ledger, scored with binary
checks (no LLM judge) per turn class:

- figure (a spending figure), follow_up (an elliptical patch), balance (an
  Account balance or overdraft), clarify (a reference code cannot settle):
  0 model calls each;
- open ("where can I save?") and in_depth ("plan my next 3 months"): one fetch
  round and exactly 1 writer call, with no tool schemas;
- miss (nothing answers it): an honest "can't yet" with chips, never a table;
- near_miss (one unread word): left to the model, never mis-planned.

Reported per class: model calls per question, binary pass rate and misses;
per turn index: prompt tokens of the writer call, which must stay flat as a
conversation grows (append-only prompt, last 3 turns, a 1.5k-token facts
cap). The map ablation (#280) runs the map turns with and without the
personal-map block: the proxy is whether the writer's context names the
planted related entity.

    uv run python evals/assistant_conversations.py           # CI: results reproduce
    uv run python evals/assistant_conversations.py --write   # refresh (see gate.py)
    uv run python evals/assistant_conversations.py --model qwen3.5:4b-q4_K_M
        # local, opt-in: real latencies, p50/p95 per class, into
        # results/assistant_conversations_local.json

CI also gates the committed local run's p95 per class (BUDGET_MS), so a
recorded slowdown fails the build without a model in CI.
"""

import json
import re
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import keyring

import gate
from assistant_bench import _MemoryKeyring, percentile  # pyright: ignore[reportPrivateUsage]
from finance_agent.assistant import (
    ChatModel,
    Message,
    OllamaChatModel,
    Reply,
    ToolSpec,
    context,
    graph,
)
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.storage import open_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic_assistant import DECEMBER, ME, OTHER, ROWS, seed  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "assistant_conversations.json"
LOCAL_RESULTS = HERE / "results" / "assistant_conversations_local.json"
DATASET_VERSION = 1
TODAY = __import__("datetime").date(2026, 1, 20)

type Class = Literal[
    "figure", "follow_up", "balance", "clarify", "open", "in_depth", "miss", "near_miss"
]
CALLS: dict[str, int] = {  # the most model calls a class may make
    "figure": 0,
    "follow_up": 0,
    "balance": 0,
    "clarify": 0,
    "open": 1,
    "in_depth": 1,
}
# p95 budgets on a real run (#317): simple answers under 1 s, open ones aim
# for 10 s, and none over 30 s
BUDGET_MS = {
    "figure": 1000,
    "follow_up": 1000,
    "balance": 1000,
    "clarify": 1000,
    "open": 15000,
    "in_depth": 30000,
}


@dataclass(frozen=True)
class Turn:
    question: str
    kind: Class
    path: str  # the path the reply must take
    says: tuple[str, ...] = ()  # every one must appear in the reply


CONVERSATIONS: dict[str, tuple[Turn, ...]] = {
    "figures-and-follow-ups": (
        Turn(
            "How much did I spend on fuel last month?", "figure", "planner", ("£84.00",)
        ),
        Turn("what about Tesco?", "follow_up", "planner", ("£36.00", "Tesco")),
        Turn("by merchant", "follow_up", "planner", ("Tesco Fuel (£36.00)",)),
        Turn("and the month before?", "follow_up", "planner", ("November 2025",)),
        Turn("and this month?", "follow_up", "planner", ("January 2026",)),
    ),
    "food-and-advice": (
        Turn(
            "How much did I spend on food in January 2026?",
            "figure",
            "planner",
            ("£62.49", "Groceries + Eating out"),
        ),
        Turn("and the month before?", "follow_up", "planner", ("December 2025",)),
        Turn("Where can I save?", "open", "local_model"),
        Turn(
            "How much did I spend at Uber in December 2025?",
            "figure",
            "planner",
            ("£16.30",),
        ),
        Turn("Plan my next 3 months", "in_depth", "local_model"),
    ),
    "balances-and-misses": (
        Turn(
            "how much overdraft am I in",
            "balance",
            "planner",
            ("None of your accounts",),
        ),
        Turn("what's my balance?", "balance", "planner", ("balance as of",)),
        Turn(
            "How much did I spend on rent this month?", "clarify", "planner", ("rent",)
        ),
        Turn("What is my credit score?", "miss", "miss", ("can't answer",)),
        # the fake writer can't call tools, so the model path ends as a miss
        Turn(
            "How much did I spend at Tesco this year, the shop and the fuel?",
            "near_miss",
            "miss",
        ),
    ),
    "map": (
        Turn("Where can I save on Shell?", "open", "local_model"),
        Turn("How am I doing?", "open", "local_model"),
        Turn(
            "How much did I spend on coffee in January 2026?",
            "figure",
            "planner",
            ("£6.75",),
        ),
        Turn("what about Deliveroo?", "follow_up", "planner", ("£23.10",)),
        Turn("Plan my next 3 months", "in_depth", "local_model"),
    ),
}
MAP_PLANTED = {("map", 0): '"Transport" (derived)'}  # Shell's derived Category


class Writer:
    """The fake writer: words the first money figure in this turn's facts, so
    it is grounded by construction; a Router or tool turn gets a short, figure-
    free reply (the near-miss and miss cases). Records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Message, ...], tuple[ToolSpec, ...]]] = []

    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        self.calls.append((tuple(messages), tuple(tools)))
        if messages and messages[0].content == context.SYSTEM["router"]:
            return Reply(content="ask")
        facts = next(
            (m.content for m in messages if m.content.startswith(context.FACTS)), ""
        )
        if not tools and facts and messages[-1].content != graph.REGENERATE:
            m = re.search(r": (£[\d,]+\.\d\d)$", facts, re.M)
            return Reply(
                content=f"Your spending so far was {m[1]}."
                if m
                else "Nothing stands out yet."
            )
        # the tool loop: a scripted model that can't call tools makes up a
        # figure, which grounding refuses (the miss path)
        return Reply(content="Your score is 720.")

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Any:
        yield self.chat(messages, tools)


def _tokens(messages: Sequence[Message]) -> int:
    return round(
        sum(len(m.model_dump_json()) for m in messages) / context.CHARS_PER_TOKEN
    )


def _run(model_for: Any, timed: bool) -> dict[str, Any]:
    prev = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())
    try:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            engine = open_db(d / "db" / "conv.db")
            try:
                seed(engine, OTHER, d)
                seed(engine, ME, d, DECEMBER + ROWS)
                return _score(engine, model_for, timed)
            finally:
                engine.dispose()
    finally:
        keyring.set_keyring(prev)


def _score(engine: Any, model_for: Any, timed: bool) -> dict[str, Any]:
    by: dict[str, dict[str, list[float]]] = {}
    fails: list[str] = []
    writer_tokens: dict[int, list[int]] = {}
    flat: list[float] = []
    misses_before = sum(graph.MISSES.values())
    map_hits = {"with_map": 0, "without_map": 0}
    for name, turns in CONVERSATIONS.items():
        for use_map in (True, False) if name == "map" else (True,):
            model = model_for()
            mine: list[int] = []
            with engine.connect() as conn:
                tx = conn.begin()
                try:
                    thread = None
                    for i, t in enumerate(turns):
                        seen = len(model.calls)
                        start = time.perf_counter()
                        a = ask(
                            Assistant(
                                model, conn, TODAY, prefetch=True, use_map=use_map
                            ),
                            t.question,
                            thread,
                        )
                        ms = (time.perf_counter() - start) * 1000
                        thread = a.thread_id
                        calls = model.calls[seen:]
                        if not use_map:  # the ablation only measures the proxy
                            if (name, i) in MAP_PLANTED and _holds(
                                calls, MAP_PLANTED[(name, i)]
                            ):
                                map_hits["without_map"] += 1
                            continue
                        if (name, i) in MAP_PLANTED and _holds(
                            calls, MAP_PLANTED[(name, i)]
                        ):
                            map_hits["with_map"] += 1
                        row = by.setdefault(t.kind, {"calls": [], "pass": [], "ms": []})
                        # a real model may answer a miss or near-miss itself, grounded
                        # (it can say it has no credit score); never with a month table.
                        # A near-miss whose answer fails grounding may fall back to
                        # the figures it fetched: only that query's rows, no month dump
                        honest = timed and (
                            (
                                t.kind in ("miss", "near_miss")
                                and a.path == "local_model"
                                and "|" not in a.reply
                            )
                            or (
                                t.kind == "near_miss"
                                and a.path == "deterministic"
                                and "spending_summary" not in a.reply
                            )
                        )
                        ok = (
                            (a.path == t.path or honest)
                            and len(calls) <= CALLS.get(t.kind, 9)
                            and (honest or all(s in a.reply for s in t.says))
                            and all(
                                tools == ()
                                for _, tools in calls
                                if t.kind in ("open", "in_depth")
                            )
                            and (
                                t.kind != "miss"
                                or honest
                                or (len(a.chips) == 3 and "|" not in a.reply)
                            )
                        )
                        row["calls"].append(len(calls))
                        row["pass"].append(float(ok))
                        row["ms"].append(ms)
                        if not ok:
                            fails.append(f"{name}#{i}")
                        if t.kind in ("open", "in_depth") and calls:
                            n = _tokens(calls[-1][0])
                            writer_tokens.setdefault(i, []).append(n)
                            mine.append(n)
                finally:
                    tx.rollback()
            if len(mine) > 1:
                flat.append(max(mine) / min(mine))
    classes: dict[str, Any] = {}
    for k, row in sorted(by.items()):
        c = {
            "turns": len(row["calls"]),
            "llm_calls_per_question": round(sum(row["calls"]) / len(row["calls"]), 6),
            "pass_rate": round(sum(row["pass"]) / len(row["pass"]), 6),
        }
        if timed:
            c |= {
                "latency_p50_ms": round(percentile(row["ms"], 50), 1),
                "latency_p95_ms": round(percentile(row["ms"], 95), 1),
            }
        classes[k] = c
    turns_all = [p for r in by.values() for p in r["pass"]]
    metrics: dict[str, float] = {
        "pass_rate": round(sum(turns_all) / len(turns_all), 6),
        "writer_prompt_growth": round(max(flat, default=1.0), 3),
        "misses": sum(graph.MISSES.values()) - misses_before,
        "map_proxy_with_map": map_hits["with_map"],
        "map_proxy_without_map": map_hits["without_map"],
    }
    for i, v in sorted(writer_tokens.items()):
        metrics[f"writer_prompt_tokens_turn_{i}"] = round(sum(v) / len(v), 1)
    for k, c in classes.items():
        for name, v in c.items():
            if name != "turns":
                metrics[f"{k}_{name}"] = v
    return {"classes": classes, "metrics": metrics, "failures": fails}


def _holds(calls: Sequence[tuple[tuple[Message, ...], Any]], text: str) -> bool:
    return any(text in m.content for msgs, _ in calls for m in msgs)


def evaluate() -> dict[str, Any]:
    return {
        "harness": "assistant_conversations",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": __import__("hashlib")
        .sha256(repr(CONVERSATIONS).encode())
        .hexdigest(),
        "conversations": len(CONVERSATIONS),
        "turns": sum(len(t) for t in CONVERSATIONS.values()),
        "prompt_sha256": __import__("hashlib")
        .sha256(json.dumps([context.SYSTEM, context.WRITER, context.LENGTH]).encode())
        .hexdigest(),
        "models": {"fake_writer": _run(Writer, timed=False)},
    }


def budget_problems(path: Path = LOCAL_RESULTS) -> list[str]:
    """Classes over their p95 budget in the committed local run, if any."""
    if not path.exists():
        return []
    out: list[str] = []
    for model, row in json.loads(path.read_text()).get("models", {}).items():
        for k, c in row["classes"].items():
            p95 = c.get("latency_p95_ms")
            if p95 is not None and k in BUDGET_MS and p95 > BUDGET_MS[k]:
                out.append(f"{model} {k}: p95 {p95} ms over {BUDGET_MS[k]} ms")
    return out


class _Timed:
    """An Ollama model with the same call record as the fake writer."""

    def __init__(self, model: str) -> None:
        self.inner = OllamaChatModel(model)
        self.calls: list[tuple[tuple[Message, ...], tuple[ToolSpec, ...]]] = []

    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        self.calls.append((tuple(messages), tuple(tools)))
        return self.inner.chat(messages, tools)

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Any:
        yield self.chat(messages, tools)


def local(model: str) -> dict[str, Any]:
    shared: ChatModel = _Timed(model)
    OllamaChatModel(model).chat([])  # load first: the chat screen warms it
    try:
        row = _run(lambda: shared, timed=True)
    finally:  # unload now: the Mac is shared
        OllamaChatModel(model, keep_alive="0").chat([])
    old: dict[str, Any] = (
        json.loads(LOCAL_RESULTS.read_text()) if LOCAL_RESULTS.exists() else {}
    )
    old.setdefault("models", {})[model] = row
    old["harness"] = "assistant_conversations_local"
    LOCAL_RESULTS.write_text(json.dumps(old, indent=2) + "\n")
    return row


def lower_is_better(name: str) -> bool:
    return (
        name.endswith(("_per_question", "_growth", "misses", "_ms", "without_map"))
        or "tokens" in name
    )


def main(argv: Sequence[str]) -> int:
    if "--model" in argv:
        m = argv[list(argv).index("--model") + 1]
        print(json.dumps(local(m)["classes"], indent=2))
        return 0
    code = gate.run("assistant_conversations", evaluate, RESULTS, lower_is_better, argv)
    for p in budget_problems():
        print(f"assistant_conversations: {p}", file=sys.stderr)
        code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
