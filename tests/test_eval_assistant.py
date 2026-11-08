"""The Assistant benchmark harness (#143): golden set, metrics, the qwen
tool-call printing detector, and the opt-in local run. Synthetic data only;
nothing here talks to Ollama."""

import json
from pathlib import Path
from typing import Any

import pytest

import assistant_bench as bench
import assistant_golden as golden
import gate
from finance_agent.assistant import FakeChatModel, Reply, ToolCall

pytestmark = pytest.mark.pre_router  # the bench runs the graph as shipped

NAMES = {"spending_summary", "category_trend"}


@pytest.mark.parametrize(
    "content",
    [
        '<tool_call>\n{"name": "spending_summary", "arguments": {}}\n</tool_call>',
        "<tool_call>\n<function=category_trend>\n<parameter=category>\nFood"
        "\n</parameter>\n</function>\n</tool_call>",
        'Sure: {"name": "spending_summary", "arguments": {"month": "2026-01"}}',
        '```json\n{"name": "category_trend", "parameters": {"category": "x"}}\n```',
        'spending_summary(month="2026-01")',
    ],
)
def test_a_tool_call_printed_as_text_is_caught(content: str) -> None:
    assert bench.printed_tool_call(content, NAMES)


@pytest.mark.parametrize(
    "content",
    [
        "You spent £92.34 in January 2026.",
        'The merchant was {"data": "TESCO"}.',
        '{"name": "not_a_tool", "arguments": {}}',
        "I looked at your spending summary for January.",
    ],
)
def test_ordinary_replies_are_not_flagged(content: str) -> None:
    assert not bench.printed_tool_call(content, NAMES)


def test_percentiles_use_the_nearest_rank() -> None:
    xs = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert bench.percentile(xs, 50) == 3.0
    assert bench.percentile(xs, 95) == 5.0
    assert bench.percentile([7.0], 95) == 7.0


# --- the harness on FakeChatModel ----------------------------------------------


@pytest.fixture(scope="module")
def results() -> dict[str, Any]:
    return bench.evaluate()


def test_the_oracle_scores_full_marks(results: dict[str, Any]) -> None:
    oracle = results["models"]["fake_oracle"]
    assert oracle["failures"] == {}
    assert oracle["metrics"]["tool_call_accuracy"] == 1.0
    assert oracle["metrics"]["grounding_pass_rate"] == 1.0
    assert oracle["metrics"]["refusal_correctness"] == 1.0
    assert oracle["metrics"]["printed_tool_call_rate"] == 0.0


def test_a_model_that_prints_its_tool_calls_is_caught(results: dict[str, Any]) -> None:
    bug = results["models"]["fake_prints_tool_calls"]
    tool_cases = {c.id for c in golden.CASES if c.calls}
    assert set(bug["failures"]["printed"]) == tool_cases
    assert set(bug["failures"]["tool_calls"]) == tool_cases
    assert bug["metrics"]["tool_call_accuracy"] < 0.5


def test_the_golden_set_covers_every_sub_graph_and_refusals() -> None:
    assert {c.route for c in golden.CASES} == {"ask", "memory", "correct", "plan"}
    assert sum(c.refusal for c in golden.CASES) >= 5
    for c in golden.CASES:
        assert not (c.refusal and any(k.name.startswith("propose_") for k in c.calls))


def test_the_committed_results_reproduce(results: dict[str, Any]) -> None:
    committed = json.loads(bench.RESULTS.read_text())
    assert gate.differences(json.loads(json.dumps(results)), committed, 0.0) == []


def test_a_model_that_proposes_on_a_refusal_fails_it() -> None:
    """Adversarial: the scripted model obeys 'remember that I spend £92.34'."""
    case = next(c for c in golden.CASES if c.id == "refuse-figure-memory")

    def obeys(c: golden.Case, ids: bench.Ids) -> bench.ChatModel:
        if c.id != case.id:
            return bench.oracle(c, ids)
        call = ToolCall(
            name="propose_memory",
            arguments={"type": "preference", "text": "spends a lot on food"},
        )
        return FakeChatModel(
            [*bench.routed(c), Reply(tool_calls=(call,)), Reply(content="OK")]
        )

    r = bench.evaluate({"obeys": ("obeys", "1", obeys)})["models"]["obeys"]
    assert r["failures"]["refusal"] == [case.id]
    assert r["failures"]["tool_calls"] == [case.id]


# --- the opt-in local run, against a fake Ollama ---------------------------------


class Wire:
    """A fake Ollama /api/chat: the Router says Ask, then it answers without tools."""

    def __init__(self, sent: list[dict[str, Any]]) -> None:
        self.sent = sent

    def request(self, method: str, url: str, body: bytes, headers: Any) -> None:
        self.sent.append(json.loads(body))

    def getresponse(self) -> "Wire":
        return self

    status = 200

    def read(self) -> bytes:
        body = self.sent[-1]
        content = "ask" if body["messages"] and "tools" not in body else "I can't."
        return json.dumps({"done": True, "message": {"content": content}}).encode()

    def close(self) -> None:
        pass


GB = 1024**3


def test_a_local_run_measures_at_8k_records_memory_and_unloads(
    tmp_path: Path,
) -> None:
    sent: list[dict[str, Any]] = []
    out = tmp_path / "local.json"
    bench.local(
        "qwen3.5:4b",
        out,
        sample=lambda: (5 * GB, 6 * GB, "sha256:abc"),
        connect=lambda: Wire(sent),
    )
    *runs, unload = sent
    assert {b["options"]["num_ctx"] for b in runs} == {8192}
    assert unload["messages"] == [] and unload["keep_alive"] == "0"
    saved = json.loads(out.read_text())["models"]["qwen3.5:4b"]
    assert saved["model_version"] == "sha256:abc"
    assert saved["num_ctx"] == 8192
    assert saved["metrics"]["peak_rss_gb"] == 5.0
    assert saved["metrics"]["peak_memory_gb"] == 6.0
    assert saved["metrics"]["latency_p95_ms"] >= saved["metrics"]["latency_p50_ms"]


def test_a_local_run_unloads_the_model_even_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("benchmark crashed")

    monkeypatch.setattr(bench, "evaluate", boom)
    with pytest.raises(RuntimeError):
        bench.local(
            "qwen3.5:4b",
            tmp_path / "local.json",
            sample=lambda: (0, 0, ""),
            connect=lambda: Wire(sent),
        )
    *warm, unload = sent  # the timed load, then the unload
    assert [w["messages"] for w in warm] == [[]]
    assert unload == {
        "model": "qwen3.5:4b",
        "messages": [],
        "stream": False,
        "keep_alive": "0",
        "think": False,
        "options": {"temperature": 0, "presence_penalty": 0, "num_ctx": 8192},
    }
    assert not (tmp_path / "local.json").exists()
