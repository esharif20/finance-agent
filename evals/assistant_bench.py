"""Assistant benchmark (#143, docs/ML_EVALUATIONS.md "Assistant quality"): the
graph on the synthetic golden set (`assistant_golden.py`), scored for route
accuracy, tool-call accuracy, grounding pass rate, refusal correctness and the
qwen tool-call printing bug, plus p50/p95 latency and peak memory on a real run.

    uv run python evals/assistant_bench.py                     # CI: results reproduce
    uv run python evals/assistant_bench.py --write             # refresh (see gate.py)
    uv run python evals/assistant_bench.py --model qwen3.5:4b  # local, opt-in
    uv run python evals/assistant_bench.py --model cloud:gemini-flash-latest
        # the real-key cloud run and judge (#305, #306): worst case first
    uv run python evals/assistant_bench.py --offline-record --write
        # stand-in cloud and judge recordings, no network

The default run also checks `results/assistant_cloud.json` (the cloud path,
replayed from `bench_chat_*.json`) and `results/judge.json` (`judge.py`).

CI runs two scripted `FakeChatModel`s: an oracle that does what each case
expects, and a negative control that prints its tool calls as text, as some
qwen3.5 builds on Ollama do; the harness must score it down and flag it.

`--model` talks to localhost Ollama only, at an 8k context, and writes
`results/assistant_local.json`, which CI never reads. It samples Ollama's memory
throughout (the runner process's RSS and Ollama's own /api/ps size) and unloads
the model afterwards (keep_alive 0). Each case runs in a rolled-back
transaction on a throwaway encrypted Ledger keyed in memory: no Keychain.
"""

import hashlib
import http.client
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import keyring
import keyring.backend

import assistant_golden as golden
import gate
from finance_agent.assistant import (
    ChatModel,
    FakeChatModel,
    Message,
    OllamaChatModel,
    OllamaError,
    Reply,
    ToolCall,
    ToolSpec,
    context,
    fast_path,
    graph,
)
from finance_agent.assistant.chat_model import printed_tool_call
from finance_agent.assistant.cloud_route import CloudRoute
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.assistant.ollama import NUM_CTX, OLLAMA_PORT
from finance_agent.assistant.tools.registry import TOOLS
from finance_agent.gateway import cost
from finance_agent.gateway.cloud import (
    FLASH_MODEL,
    CloudChatModel,
    CloudError,
    RecordedTransport,
    Transport,
    Usage,
    _model_of,  # pyright: ignore[reportPrivateUsage]
    load_recording,
    recording_key,
)
from finance_agent.gateway.types import MONTH_TEXT, PLACEHOLDER
from finance_agent.storage import open_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic_assistant import DECEMBER, ME, OTHER, ROWS, seed  # noqa: E402

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "assistant_fake.json"
LOCAL_REFERENCE = "qwen3.5:9b-q4_K_M"
LOCAL_RESULTS = HERE / "results" / "assistant_local.json"

SAMPLE_EVERY = 0.25  # seconds between memory samples
GB = 1024**3

PHASES = ("router", "tool_turn", "answer_turn", "regenerate")
BY_ID = {c.id: c for c in golden.CASES}
PREFETCH = True  # retrieve-then-generate, as the app runs it

type Ids = dict[str, int]
type Factory = Callable[[golden.Case, Ids], ChatModel]


class Recorder:
    """Wraps a ChatModel: the tool calls it asked for, any it printed, and the
    wall time of each model call by phase (PHASES)."""

    def __init__(self, model: ChatModel) -> None:
        self.model = model
        self.calls: list[ToolCall] = []
        self.printed = False
        self.errors = 0
        self.phases: dict[str, list[float]] = {p: [] for p in PHASES}
        self.llm_calls = 0
        self.prompt_chars = 0  # every message and tool spec sent, as JSON

    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        self.llm_calls += 1
        self.prompt_chars += sum(len(m.model_dump_json()) for m in messages)
        self.prompt_chars += sum(len(t.model_dump_json()) for t in tools)
        start = time.perf_counter()
        try:
            r = self.model.chat(messages, tools)
        except (OllamaError, OSError):
            self.errors += 1
            raise
        took = time.perf_counter() - start
        if not tools:  # the Router and the regeneration are offered none
            router = bool(messages) and messages[0].content == context.SYSTEM["router"]
            phase = "router" if router else "regenerate"
        else:
            phase = "tool_turn" if r.tool_calls else "answer_turn"
        self.phases[phase].append(took)
        if tools:
            self.calls += r.tool_calls
        self.printed |= printed_tool_call(r.content, frozenset(TOOLS))
        return r

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Iterator[Reply]:
        yield self.chat(messages, tools)


# --- scoring ----------------------------------------------------------------


def _resolve(v: Any, ids: Ids) -> Any:
    if isinstance(v, str) and v.startswith(golden.TXN):
        return ids[v.removeprefix(golden.TXN)]
    if isinstance(v, dict):
        return {k: _resolve(x, ids) for k, x in v.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, list):
        return [_resolve(x, ids) for x in v]  # pyright: ignore[reportUnknownVariableType]
    return v


def _match(want: Any, got: Any) -> bool:
    if isinstance(want, dict):
        return isinstance(got, dict) and all(
            k in got and _match(v, got[k])  # pyright: ignore[reportUnknownArgumentType]
            for k, v in want.items()  # pyright: ignore[reportUnknownVariableType]
        )
    if isinstance(want, list):
        return (
            isinstance(got, list)
            and len(want) == len(got)  # pyright: ignore[reportUnknownArgumentType]
            and all(map(_match, want, got))  # pyright: ignore[reportUnknownArgumentType]
        )
    if isinstance(want, str):
        return isinstance(got, str) and want.casefold() in got.casefold()
    return type(want) is type(got) and want == got


def calls_match(want: Sequence[golden.Call], got: Sequence[ToolCall], ids: Ids) -> bool:
    return len(want) == len(got) and all(
        w.name == g.name and _match(_resolve(w.args, ids), g.arguments)
        for w, g in zip(want, got, strict=True)
    )


def percentile(xs: Sequence[float], p: float) -> float:
    """Nearest-rank percentile."""
    s = sorted(xs)
    return s[max(0, math.ceil(len(s) * p / 100) - 1)]


# --- the scripted models ------------------------------------------------------


def routed(case: golden.Case) -> list[Reply]:
    """The Router's reply, unless the keyword pre-router answers without it."""
    return [] if graph.pre_route(case.question) else [Reply(content=case.route)]


def planned(case: golden.Case) -> ToolCall | None:
    """The fast path's call for a planner-parsable case: no model call then.
    A cloud run has none: an opted-in thread keeps cloud wording."""
    return None if _cloud_run else fast_path.parse(case.question, golden.TODAY)


_cloud_run = False  # set by evaluate() for the length of a cloud run


def _oracle_turn(case: golden.Case, ids: Ids) -> list[Reply]:
    if planned(case):
        return []
    script = routed(case)
    script += [
        Reply(tool_calls=(ToolCall(name=c.name, arguments=_resolve(c.args, ids)),))
        for c in case.calls
    ]
    return [*script, Reply(content=case.answer)]


def _before(case: golden.Case, ids: Ids) -> list[Reply]:
    """The oracle's script for the turn a follow-up case follows."""
    return _oracle_turn(BY_ID[case.after], ids) if case.after else []


def oracle(case: golden.Case, ids: Ids) -> ChatModel:
    return FakeChatModel([*_before(case, ids), *_oracle_turn(case, ids)])


def prints_tool_calls(case: golden.Case, ids: Ids) -> ChatModel:
    """The bug: each tool call comes back as text, in qwen's Hermes format."""
    printed = [
        Reply(
            content="<tool_call>\n"
            + json.dumps({"name": c.name, "arguments": _resolve(c.args, ids)})
            + "\n</tool_call>"
        )
        for c in case.calls
    ]
    tail = printed or [Reply(content=case.answer)]
    # padded: the graph may regenerate after a printed call fails grounding
    return FakeChatModel([*_before(case, ids), *routed(case), *tail, *tail, *tail])


def from_context(case: golden.Case, ids: Ids) -> ChatModel:
    """Retrieve-then-generate's best case: the expected answer in one call,
    never a tool. Its grounding rate is how often the prefetched context
    alone holds the figures; the rest need the tool loop."""
    turn = [] if planned(case) else [*routed(case), *[Reply(content=case.answer)] * 2]
    return FakeChatModel([*_before(case, ids), *turn])


FAKES: dict[str, tuple[str, str, Factory]] = {
    "fake_oracle": ("FakeChatModel oracle", "1", oracle),
    "fake_prints_tool_calls": ("FakeChatModel printing bug", "1", prints_tool_calls),
    "fake_from_context": ("FakeChatModel answering from context", "1", from_context),
}

# --- the run ------------------------------------------------------------------


class _MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.items.pop((service, username), None)


def _score(
    factory: Factory,
    engine: Any,
    ids: Ids,
    timed: bool,
    gateway: CloudRoute | None = None,
    replies: list[dict[str, str]] | None = None,
    cases: tuple[golden.Case, ...] = golden.CASES,
) -> tuple[dict[str, float], dict[str, list[str]]]:
    """With `gateway`, every turn opts in to the cloud: `factory` then only
    routes and calls tools, and grounding counts cloud-worded replies only."""
    fails: dict[str, list[str]] = {
        k: []
        for k in (
            "route",
            "tool_calls",
            "grounding",
            "refusal",
            "refusal_no_proposal",
            "printed",
            "errors",
            "fallback",
        )
    }
    to_cloud = 0
    latencies: list[float] = []
    phases: dict[str, list[float]] = {p: [] for p in PHASES}
    llm: dict[str, int] = {}  # model calls in each case's scored turn
    prompt_chars = 0
    for case in cases:
        rec = Recorder(factory(case, ids))
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                thread = None
                if case.after:  # the earlier turn of the conversation, unscored
                    first = BY_ID[case.after].question
                    turn = Assistant(
                        rec, conn, golden.TODAY, cloud=gateway, prefetch=PREFETCH
                    )
                    thread = ask(turn, first, None, gateway is not None).thread_id
                    rec = Recorder(rec.model)
                start = time.perf_counter()
                turn = Assistant(
                    rec, conn, golden.TODAY, cloud=gateway, prefetch=PREFETCH
                )
                optin = None if case.after else gateway is not None
                a = ask(turn, case.question, thread, optin)
                latencies.append(time.perf_counter() - start)
                llm[case.id] = rec.llm_calls
                prompt_chars += rec.prompt_chars
                for p in PHASES:
                    phases[p] += rec.phases[p]
            finally:
                tx.rollback()  # Proposals, cache and checkpoints: gone
        proposed = a.proposal_id is not None or any(
            c.name.startswith("propose_") for c in rec.calls
        )
        checks = {
            "route": a.route == case.route,
            "tool_calls": calls_match(
                case.calls, rec.calls or [c for c in [planned(case)] if c], ids
            ),
            "printed": not rec.printed,
            "errors": not rec.errors,
        }
        if case.refusal:
            checks["refusal"] = not proposed  # the model never even asked
            checks["refusal_no_proposal"] = a.proposal_id is None  # nothing waits
        else:
            worded = gateway is not None and case.route in graph.CLOUD
            want = "cloud" if worded else "local_model"
            checks["grounding"] = a.path in (want, "planner") and all(
                f in a.reply for f in case.figures
            )
        if gateway is not None and a.cost_pence is not None:  # the cloud was asked
            to_cloud += 1
            checks["fallback"] = a.path == "cloud"
        if replies is not None:
            replies.append({"case_id": case.id, "path": a.path, "reply": a.reply})
        if dump := os.environ.get(
            "ASSISTANT_BENCH_REPLIES"
        ):  # synthetic, for diagnosis
            with open(dump, "a") as f:
                f.write(
                    json.dumps({"id": case.id, "path": a.path, "reply": a.reply}) + "\n"
                )
        for k, ok in checks.items():
            if not ok:
                fails[k].append(case.id)
    n = len(cases)
    refusals = sum(c.refusal for c in cases)

    def rate(k: str, of: int = n) -> float:
        return round(1 - len(fails[k]) / of, 6) if of else 1.0

    metrics = {
        "route_accuracy": rate("route"),
        "tool_call_accuracy": rate("tool_calls"),
        "grounding_pass_rate": rate("grounding", n - refusals),
        "refusal_correctness": rate("refusal", refusals),
        "refusal_no_proposal": rate("refusal_no_proposal", refusals),
        "printed_tool_call_rate": round(len(fails["printed"]) / n, 6),
        "model_error_rate": round(len(fails["errors"]) / n, 6),
        # cost per question, deterministic on the fakes (tokens: 4 chars each)
        "llm_calls_per_turn": round(sum(llm.values()) / n, 6),
        "prompt_tokens_per_turn": round(prompt_chars / context.CHARS_PER_TOKEN / n, 1),
        "planner_cases": sum(1 for c in cases if planned(c)),
        # the gate is 0: a question the planner parses never waits on a model
        "planner_llm_calls": sum(llm[c.id] for c in cases if planned(c)),
    }
    if gateway is not None:
        metrics["fallback_rate"] = round(len(fails["fallback"]) / max(to_cloud, 1), 6)
    if timed:  # wall clock: real runs only, so CI's results stay reproducible
        ms = [t * 1000 for t in latencies]
        metrics |= {
            "latency_p50_ms": round(percentile(ms, 50), 1),
            "latency_p95_ms": round(percentile(ms, 95), 1),
        }
        for p, ts in phases.items():  # where a turn's time goes
            if ts:
                metrics[f"{p}_p50_ms"] = round(
                    percentile([t * 1000 for t in ts], 50), 1
                )
            metrics[f"{p}_calls_per_turn"] = round(len(ts) / n, 3)
    return metrics, {k: v for k, v in fails.items() if v}


def evaluate(
    models: dict[str, tuple[str, str, Factory]] = FAKES,
    timed: bool = False,
    gateway: CloudRoute | None = None,
    replies: list[dict[str, str]] | None = None,
    cases: tuple[golden.Case, ...] = golden.CASES,  # the routing dataset's (#283)
) -> dict[str, Any]:
    global _cloud_run
    prev = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())  # never the real Keychain
    _cloud_run = gateway is not None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            engine = open_db(d / "db" / "bench.db")
            try:
                seed(engine, OTHER, d)
                ids = seed(engine, ME, d, DECEMBER + ROWS)
                out: dict[str, Any] = {}
                for key, (name, version, factory) in models.items():
                    metrics, fails = _score(
                        factory, engine, ids, timed, gateway, replies, cases
                    )
                    out[key] = {
                        "model": name,
                        "model_version": version,
                        "metrics": metrics,
                        "failures": fails,
                    }
            finally:
                engine.dispose()
    finally:
        keyring.set_keyring(prev)
        _cloud_run = False
    return {
        "harness": "assistant",
        "dataset": "assistant_golden",
        "dataset_version": golden.DATASET_VERSION,
        "dataset_sha256": golden.fingerprint(),
        "prompt_sha256": hashlib.sha256(
            json.dumps(context.SYSTEM, sort_keys=True).encode()
        ).hexdigest(),
        "cases": len(golden.CASES),
        "refusal_cases": sum(c.refusal for c in golden.CASES),
        "models": out,
    }


def lower_is_better(name: str) -> bool:
    lower = name.endswith(
        ("_rate", "_ms", "_gb", "_count", "_pence", "_per_turn", "_llm_calls")
    )
    return lower and name != "grounding_pass_rate"


# --- the cloud run (#305) -------------------------------------------------------
#
# The local driver (the oracle, so route and tools are fixed) routes and calls
# tools; the cloud model only words the reply, through #301's path, so its row
# scores grounding, refusal, leaks, fallback, cost and latency. Recordings are
# `bench_chat_<model>-*.json`, and CI replays them. Spend reserves on
# `synthetic_judge`. Only the synthetic golden set is ever sent.

CLOUD_RESULTS = HERE / "results" / "assistant_cloud.json"
RECORDINGS = HERE.parent / "tests" / "fixtures" / "synthetic" / "cloud"
GOLDEN_RECORDINGS = RECORDINGS / "golden"  # #301's; the offline stand-in replays them
CHAT = "bench_chat_"
BUCKET = "synthetic_judge"
MONTHLY_CAP_PENCE = 900  # the user's £9 a month for all cloud work (#292)
STANDIN = "offline-standin"  # the modelVersion of a reply no cloud model wrote
# every word of the synthetic Ledger's bank text: none may leave
BANK_WORDS = frozenset(
    w
    for _, _, desc, _ in ROWS + DECEMBER
    for w in re.findall(r"[a-z]{3,}", desc.lower())
) - {"ltd", "trip", "travel", "stores"}


def leaked(payload: dict[str, Any]) -> bool:
    """A figure (outside a placeholder or a YYYY-MM month) or a bank word left."""
    text = json.dumps(payload)
    bare = MONTH_TEXT.sub("", PLACEHOLDER.sub("", text))
    words = set(re.findall(r"[a-z]{3,}", text.lower()))
    return any(ch.isdigit() for ch in bare) or bool(BANK_WORDS & words)


class BenchMeter:
    """`cloud_route.Meter` on the `synthetic_judge` bucket. Alone it meters in
    memory (CI); with `inner` (the real `SpendStore`) it reserves there. It
    keeps payloads to count leaks and never adds them to the user's log."""

    def __init__(self, inner: Any = None) -> None:
        self.inner = inner
        self.committed = self.pence = self.calls = 0
        self.asks: list[tuple[int, int]] = []  # (max_in, max_out) per reservation
        self.payloads: list[dict[str, Any]] = []

    def reserve(
        self, bucket: str, model: str, max_in: int, max_out: int
    ) -> cost.Allowed | cost.Warn | cost.Denied:
        self.asks.append((max_in, max_out))
        if self.inner is not None:
            return self.inner.reserve(BUCKET, model, max_in, max_out)
        now = datetime.now(UTC)
        d = cost.reserve(
            BUCKET, model, max_in, max_out, committed=self.committed, now=now
        )
        if not isinstance(d, cost.Denied):
            self.committed += d.reservation.pence
        return d

    def settle(self, reservation: cost.Reservation, usage: Usage) -> int:
        c = (self.inner or cost).settle(reservation, usage)
        if self.inner is None:
            self.committed += c - reservation.pence  # as SpendStore does
        self.pence += c
        self.calls += 1
        return c

    def log_payload(
        self,
        user_id: int,
        conversation_id: str,
        payload: dict[str, Any],
        model: str,
        cost_pence: int,
    ) -> None:
        self.payloads.append(payload)


def recordings(prefix: str) -> list[dict[str, Any]]:
    return [load_recording(p) for p in sorted(RECORDINGS.glob(f"{prefix}*.json"))]


@dataclass
class Tape:
    """Replays `<prefix>*.json` (an unknown request fails) and collects the
    latency and `modelVersion` each served reply recorded."""

    prefix: str
    latencies: list[float] = field(default_factory=list[float])
    versions: set[str] = field(default_factory=set[str])

    def __post_init__(self) -> None:
        self._replies = {
            recording_key(r["model"], r["request"]): r for r in recordings(self.prefix)
        }

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes
    ) -> tuple[int, bytes]:
        rec = self._replies.get(recording_key(_model_of(url), json.loads(body)))
        if rec is None:
            raise LookupError("no recording for this request")
        if "latency_ms" in rec:
            self.latencies.append(rec["latency_ms"])
        self.versions.add(rec["response"].get("modelVersion", ""))
        return 200, json.dumps(rec["response"]).encode()


def writer(
    prefix: str, inner: Transport, secret: str = "", standin: bool = False
) -> Transport:
    """Calls `inner` and writes each 200 exchange as `<prefix><key>.json`, with
    its wall time on a live run. A stand-in reply is marked as one."""

    def call(url: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        start = time.perf_counter()
        status, data = inner(url, headers, body)
        ms = round((time.perf_counter() - start) * 1000, 1)
        if status != 200:
            return status, data
        model, request, response = _model_of(url), json.loads(body), json.loads(data)
        rec: dict[str, Any] = {"model": model, "request": request}
        if standin:
            response["modelVersion"] = STANDIN
        else:
            rec["latency_ms"] = ms
        rec["response"] = response
        text = "# synthetic\n" + json.dumps(rec, indent=1) + "\n"
        if secret and secret in text:
            raise CloudError("a recording would hold the key")
        key = recording_key(model, request)[:12]
        (RECORDINGS / f"{prefix}{key}.json").write_text(text)
        return status, json.dumps(response).encode()

    return call


def fake_key() -> str:
    return "fake-" + "x" * 8  # built at run time: no key-shaped literal


def cloud_run(
    model: str = FLASH_MODEL,
    transport: Transport | None = None,
    meter: BenchMeter | None = None,
    replies: list[dict[str, str]] | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    """One cloud model's row, replayed from its recordings unless `transport`."""
    tape = Tape(f"{CHAT}{model}-")
    meter = meter or BenchMeter()
    chat = CloudChatModel(key or fake_key(), transport=transport or tape)
    name = f"cloud:{model}"
    out = evaluate(
        {name: (name, "", oracle)},
        gateway=CloudRoute(chat, meter, model=model),
        replies=replies,
    )
    row = out["models"][name]
    row["model_version"] = ",".join(sorted(tape.versions))
    row["metrics"] |= {
        "leak_count": sum(map(leaked, meter.payloads)),
        "cloud_calls": meter.calls,
        "cost_total_pence": meter.pence,
        "cost_per_case_pence": round(meter.pence / len(golden.CASES), 4),
    }
    if tape.latencies:  # only a live run records them
        row["metrics"] |= {
            "cloud_latency_p50_ms": round(percentile(tape.latencies, 50), 1),
            "cloud_latency_p95_ms": round(percentile(tape.latencies, 95), 1),
        }
    return out


def default_model(rows: dict[str, Any]) -> str:
    """The rule (#305): highest grounding, then lowest cost per case."""

    def rank(kv: tuple[str, Any]) -> tuple[float, float]:
        m = kv[1]["metrics"]
        return -m["grounding_pass_rate"], m["cost_per_case_pence"]

    return min(rows.items(), key=rank)[0].removeprefix("cloud:")


def cloud_models() -> list[str]:
    return sorted({r["model"] for r in recordings(CHAT)})


def cloud_evaluate(replies: list[dict[str, str]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for m in cloud_models():
        fresh = cloud_run(m, replies=replies)
        out = fresh | {"models": out.get("models", {}) | fresh["models"]}
    out["harness"] = "assistant_cloud"
    out["default_cloud_model"] = default_model(out["models"])
    local: dict[str, Any] = (
        json.loads(LOCAL_RESULTS.read_text()) if LOCAL_RESULTS.exists() else {}
    )
    ref = local.get("models", {}).get(LOCAL_REFERENCE)
    if ref is not None:  # read-only: the row the cloud models are compared with
        out["local_reference"] = {"model": LOCAL_REFERENCE, "metrics": ref["metrics"]}
    return out


def cloud_replies() -> list[dict[str, str]]:
    """Every cloud model's replies, replayed: what the judge scores."""
    out: list[dict[str, str]] = []
    for m in cloud_models():
        got: list[dict[str, str]] = []
        cloud_run(m, replies=got)
        out += [r | {"model": m} for r in got if r["path"] == "cloud"]
    return out


def worst_case_pence(model: str) -> tuple[int, int]:
    """(calls, worst-case pence) for a cloud run: every reservation a replay
    makes (the request bodies do not depend on the model), at `model`'s price."""
    meter = BenchMeter()
    cloud_run(FLASH_MODEL, meter=meter)
    return len(meter.asks), sum(cost.pence(model, i, o) for i, o in meter.asks)


# --- the local run --------------------------------------------------------------


def _get(path: str) -> Any:
    conn = http.client.HTTPConnection("localhost", OLLAMA_PORT, timeout=5)
    try:
        conn.request("GET", path)
        return json.loads(conn.getresponse().read())
    finally:
        conn.close()


def _runner_rss() -> int:
    """Bytes resident in Ollama's model runner (the largest such process)."""
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["/bin/ps", "-axo", "rss=,command="], capture_output=True, text=True
    ).stdout
    rss = [
        int(line.split(None, 1)[0]) * 1024
        for line in out.splitlines()
        if "ollama" in line and ("runner" in line or "llama-server" in line)
    ]
    return max(rss, default=0)


def sample_ollama(model: str) -> tuple[int, int, str]:
    """(runner RSS, Ollama's /api/ps size, model digest) right now."""
    loaded = [
        m
        for m in _get("/api/ps").get("models", [])
        if model in (m.get("name"), m.get("model"))
    ]
    size = max((m.get("size", 0) for m in loaded), default=0)
    digest = next((m.get("digest", "") for m in loaded), "")
    return _runner_rss(), size, digest


class Peak:
    """Samples memory on a thread until stopped; keeps the peaks."""

    def __init__(self, sample: Callable[[], tuple[int, int, str]]) -> None:
        self._sample = sample
        self._stop = threading.Event()
        self.rss = self.size = 0
        self.digest = ""
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while True:
            try:
                rss, size, digest = self._sample()
                self.rss, self.size = max(self.rss, rss), max(self.size, size)
                self.digest = digest or self.digest
            except (OSError, ValueError, http.client.HTTPException):
                pass  # one missed sample; the next one counts
            if self._stop.wait(SAMPLE_EVERY):
                return

    def __enter__(self) -> "Peak":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join()


def local(
    model: str,
    path: Path = LOCAL_RESULTS,
    sample: Callable[[], tuple[int, int, str]] | None = None,
    connect: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Benchmark one Ollama model; merge its scores into `path`."""
    chat = OllamaChatModel(model, connect=connect)
    try:
        with Peak(sample or (lambda: sample_ollama(model))) as peak:
            start = time.perf_counter()
            chat.chat([])  # loads the model, timed apart: the chat screen warms it
            load_ms = round((time.perf_counter() - start) * 1000, 1)
            fresh = evaluate({model: (model, "", lambda _c, _i: chat)}, timed=True)
    finally:
        try:  # an empty chat with keep_alive 0 unloads the model
            OllamaChatModel(model, keep_alive="0", connect=connect).chat([])
        except OllamaError:
            print(f"assistant eval: could not unload {model}", file=sys.stderr)
    entry = fresh["models"][model]
    entry["model_version"] = peak.digest
    entry["num_ctx"] = NUM_CTX
    entry["metrics"] |= {
        "model_load_ms": load_ms,
        "peak_rss_gb": round(peak.rss / GB, 3),
        "peak_ollama_size_gb": round(peak.size / GB, 3),
        "peak_memory_gb": round(max(peak.rss, peak.size) / GB, 3),
    }
    old: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    same = {k: old.get(k) for k in ("dataset_sha256", "prompt_sha256")} == {
        k: fresh[k] for k in ("dataset_sha256", "prompt_sha256")
    }
    fresh["models"] = (old["models"] if same else {}) | {model: entry}
    path.write_text(json.dumps(fresh, indent=2) + "\n")
    return fresh


def main(argv: Sequence[str]) -> int:
    if "--model" in argv:
        i = list(argv).index("--model")
        if i + 1 >= len(argv):
            print("usage: assistant_bench.py --model <ollama model>", file=sys.stderr)
            return 2
        m = argv[i + 1]
        if m.startswith("cloud:"):
            return live(m.removeprefix("cloud:"), "--yes" in argv)
        print(json.dumps(local(m)["models"][m]["metrics"], indent=2))
        return 0
    import judge  # it imports this module

    if "--offline-record" in argv:  # stand-in recordings from #301's, no network
        for p in RECORDINGS.glob(f"{CHAT}{FLASH_MODEL}-*.json"):
            p.unlink()
        inner = RecordedTransport(GOLDEN_RECORDINGS)
        cloud_run(FLASH_MODEL, writer(f"{CHAT}{FLASH_MODEL}-", inner, standin=True))
        judge.record(judge.offline_transport, standin=True)
    codes = [
        gate.run("assistant", evaluate, RESULTS, lower_is_better, argv),
        gate.run(
            "assistant_cloud", cloud_evaluate, CLOUD_RESULTS, lower_is_better, argv
        ),
        judge.main([a for a in argv if a != "--offline-record"]),
    ]
    return max(codes)


def live(model: str, yes: bool) -> int:
    """The real-key run (#306): prints the worst case first and refuses if it
    is over the £9 monthly cap; the meter also stops at `synthetic_judge`'s
    cap. It sends the synthetic golden set to `model` and the judge's calls to
    Gemini Pro, then rewrites the recordings and both results from the replies.
    A call the meter denied leaves no recording, so CI's replay then fails:
    rerun next month rather than commit that."""
    import judge
    from finance_agent.gateway.cloud import urllib_transport
    from finance_agent.storage import default_db_path, keychain
    from finance_agent.storage.spend import SpendStore

    if os.environ.get("CI"):
        sys.exit("the live cloud benchmark never runs in CI")
    store = SpendStore.beside(default_db_path())
    n_chat, chat_p = worst_case_pence(model)
    n_judge, judge_p = judge.worst_case_pence(n_chat)
    status = next(b for b in store.month_status() if b.bucket == BUCKET)
    left = status.cap_pence - status.spent_pence - status.reserved_pence
    print(
        f"assistant cloud bench: worst case {chat_p + judge_p} pence "
        f"({n_chat} calls on {model}: {chat_p}p; {n_judge} judge calls on "
        f"{judge.PRO_MODEL}: {judge_p}p); {BUCKET} has {left}p of "
        f"{status.cap_pence}p left this month (the meter stops there); monthly "
        f"cap {MONTHLY_CAP_PENCE}p",
        flush=True,
    )
    if chat_p + judge_p > MONTHLY_CAP_PENCE:
        print("not run: the worst case is over the monthly cap", file=sys.stderr)
        return 1
    if not yes and input("send the synthetic golden set? [y/N] ").lower() != "y":
        return 1
    key = keychain.gemini_key()
    if key is None:
        sys.exit("no Gemini key in the Keychain (fa keys)")
    prefix = f"{CHAT}{model}-"
    for p in RECORDINGS.glob(f"{prefix}*.json"):
        p.unlink()
    meter = BenchMeter(store)
    cloud_run(model, writer(prefix, urllib_transport, key), meter, key=key)
    judge.record(urllib_transport, key, meter=meter)
    print(f"assistant cloud bench: spent {meter.pence} pence", flush=True)
    write = ["--write", "--accept-lower"]  # a real run replaces the stand-in
    return max(
        gate.run(
            "assistant_cloud", cloud_evaluate, CLOUD_RESULTS, lower_is_better, write
        ),
        judge.main(write),
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
