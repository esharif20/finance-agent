"""Assistant benchmark (#143, docs/ML_EVALUATIONS.md "Assistant quality"): the
graph on the synthetic golden set (`assistant_golden.py`), scored for route
accuracy, tool-call accuracy, grounding pass rate, refusal correctness and the
qwen tool-call printing bug, plus p50/p95 latency and peak memory on a real run.

    uv run python evals/assistant_bench.py                     # CI: results reproduce
    uv run python evals/assistant_bench.py --write             # refresh (see gate.py)
    uv run python evals/assistant_bench.py --model qwen3.5:4b  # local, opt-in

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
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
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
)
from finance_agent.assistant.chat_model import printed_tool_call
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.assistant.ollama import OLLAMA_PORT
from finance_agent.assistant.tools.registry import TOOLS
from finance_agent.storage import open_db

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic_assistant import ME, OTHER, seed  # noqa: E402 - lives with the tests

HERE = Path(__file__).parent
RESULTS = HERE / "results" / "assistant_fake.json"
LOCAL_RESULTS = HERE / "results" / "assistant_local.json"
NUM_CTX = 8192  # the memory gate is about 7 GB at 8k context (#144)
SAMPLE_EVERY = 0.25  # seconds between memory samples
GB = 1024**3

type Ids = dict[str, int]
type Factory = Callable[[golden.Case, Ids], ChatModel]


class Recorder:
    """Wraps a ChatModel: the tool calls it asked for, and any it printed."""

    def __init__(self, model: ChatModel) -> None:
        self.model = model
        self.calls: list[ToolCall] = []
        self.printed = False
        self.errors = 0

    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        try:
            r = self.model.chat(messages, tools)
        except (OllamaError, OSError):
            self.errors += 1
            raise
        if tools:  # the Router and the regeneration are offered none
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


def oracle(case: golden.Case, ids: Ids) -> ChatModel:
    script = [Reply(content=case.route)]
    if case.route != "plan":
        script += [
            Reply(tool_calls=(ToolCall(name=c.name, arguments=_resolve(c.args, ids)),))
            for c in case.calls
        ]
        script.append(Reply(content=case.answer))
    return FakeChatModel(script)


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
    return FakeChatModel([Reply(content=case.route), *tail, *tail, *tail])


FAKES: dict[str, tuple[str, str, Factory]] = {
    "fake_oracle": ("FakeChatModel oracle", "1", oracle),
    "fake_prints_tool_calls": ("FakeChatModel printing bug", "1", prints_tool_calls),
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
    factory: Factory, engine: Any, ids: Ids, timed: bool
) -> tuple[dict[str, float], dict[str, list[str]]]:
    fails: dict[str, list[str]] = {
        k: []
        for k in ("route", "tool_calls", "grounding", "refusal", "printed", "errors")
    }
    latencies: list[float] = []
    for case in golden.CASES:
        rec = Recorder(factory(case, ids))
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                start = time.perf_counter()
                a = ask(Assistant(rec, conn, golden.TODAY), case.question)
                latencies.append(time.perf_counter() - start)
            finally:
                tx.rollback()  # Proposals, cache and checkpoints: gone
        proposed = a.proposal_id is not None or any(
            c.name.startswith("propose_") for c in rec.calls
        )
        checks = {
            "route": a.route == case.route,
            "tool_calls": calls_match(case.calls, rec.calls, ids),
            "printed": not rec.printed,
            "errors": not rec.errors,
        }
        if case.refusal:
            checks["refusal"] = not proposed
        else:
            checks["grounding"] = a.path == "local_model" and all(
                f in a.reply for f in case.figures
            )
        for k, ok in checks.items():
            if not ok:
                fails[k].append(case.id)
    n = len(golden.CASES)
    refusals = sum(c.refusal for c in golden.CASES)

    def rate(k: str, of: int = n) -> float:
        return round(1 - len(fails[k]) / of, 6)

    metrics = {
        "route_accuracy": rate("route"),
        "tool_call_accuracy": rate("tool_calls"),
        "grounding_pass_rate": rate("grounding", n - refusals),
        "refusal_correctness": rate("refusal", refusals),
        "printed_tool_call_rate": round(len(fails["printed"]) / n, 6),
        "model_error_rate": round(len(fails["errors"]) / n, 6),
    }
    if timed:  # wall clock: real runs only, so CI's results stay reproducible
        ms = [t * 1000 for t in latencies]
        metrics |= {
            "latency_p50_ms": round(percentile(ms, 50), 1),
            "latency_p95_ms": round(percentile(ms, 95), 1),
        }
    return metrics, {k: v for k, v in fails.items() if v}


def evaluate(
    models: dict[str, tuple[str, str, Factory]] = FAKES, timed: bool = False
) -> dict[str, Any]:
    prev = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())  # never the real Keychain
    try:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            engine = open_db(d / "db" / "bench.db")
            try:
                seed(engine, OTHER, d)
                ids = seed(engine, ME, d)
                out: dict[str, Any] = {}
                for key, (name, version, factory) in models.items():
                    metrics, fails = _score(factory, engine, ids, timed)
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
    return name.endswith(("_rate", "_ms", "_gb")) and name != "grounding_pass_rate"


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
    chat = OllamaChatModel(model, num_ctx=NUM_CTX, connect=connect)
    try:
        with Peak(sample or (lambda: sample_ollama(model))) as peak:
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
        print(json.dumps(local(m)["models"][m]["metrics"], indent=2))
        return 0
    return gate.run("assistant", evaluate, RESULTS, lower_is_better, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
