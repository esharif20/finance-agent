"""The cloud benchmark and the rubric judge (#305), on recordings only: no
call leaves, and the key is built at runtime."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

import assistant_bench as bench
import gate
import judge
from finance_agent.gateway.cloud import CloudChatModel, FakeCloud
from finance_agent.gateway.types import GatewayPayload

pytestmark = pytest.mark.pre_router  # the golden set runs the graph as shipped


def _committed(path: Path) -> Any:
    return json.loads(path.read_text())


def test_cloud_results_reproduce_from_recordings() -> None:
    fresh = json.loads(json.dumps(bench.cloud_evaluate()))
    assert gate.differences(fresh, _committed(bench.CLOUD_RESULTS), 0.0) == []
    row = fresh["models"]["cloud:gemini-flash-latest"]["metrics"]
    assert row["leak_count"] == 0
    for k in ("grounding_pass_rate", "refusal_correctness", "fallback_rate"):
        assert k in row
    assert fresh["default_cloud_model"] == "gemini-flash-latest"


def test_judge_results_reproduce_from_recordings() -> None:
    fresh = json.loads(json.dumps(judge.evaluate()))
    assert gate.differences(fresh, _committed(judge.RESULTS), 0.0) == []
    m = fresh["models"]["judge:gemini-pro-latest"]["metrics"]
    assert (m["kappa"] >= judge.KAPPA_MIN) == (fresh["status"] == "calibrated")
    assert ("replies" in fresh) == (fresh["status"] == "calibrated")


def _sent(prefix: str) -> list[dict[str, Any]]:
    return [
        json.loads(r["request"]["contents"][0]["parts"][0]["text"])
        for r in bench.recordings(prefix)
    ]


def test_no_recorded_payload_leaks() -> None:
    sent = _sent(bench.CHAT) + _sent(judge.PREFIX)
    assert len(sent) >= 23 + 30
    assert [p for p in sent if bench.leaked(p)] == []
    for p in sent:
        GatewayPayload.model_validate_json(json.dumps(p))  # passes the payload check


@pytest.mark.parametrize(
    "bad",
    [
        {"question": "you spent 92.34"},
        {"facts": [{"field": "total", "value": "84.00"}]},
        {"question": "SHELL took it"},  # a word of the synthetic bank text
    ],
)
def test_leak_check_catches_a_figure_or_a_bank_word(bad: dict[str, Any]) -> None:
    assert bench.leaked({"intent": "answer", "question": "{P1} in 2026-01"}) is False
    assert bench.leaked({"intent": "answer"} | bad) is True


class Spy:
    def __init__(self, reply: str = '{"score": 3}') -> None:
        self.calls = 0
        self.fake = FakeCloud(text=reply)

    def __call__(self, url: str, headers: dict[str, str], body: bytes) -> Any:
        self.calls += 1
        return self.fake(url, headers, body)


def test_judge_refuses_a_reply_not_from_the_golden_set() -> None:
    spy = Spy()
    cloud = CloudChatModel(bench.fake_key(), transport=spy)
    items = [("ask-spent-january", "You spent £92.34."), ("real-user-42", "hi")]
    with pytest.raises(judge.NotGolden):
        judge.judge(cloud, items, bench.BenchMeter())
    assert spy.calls == 0  # refused before anything was sent


def test_an_invented_figure_leaves_as_an_unknown_placeholder() -> None:
    p = judge.payload("ask-in-and-out", "£2,500.00 in, £92.34 out, so £2,400 saved.")
    assert p.question == "{P1} in, {P2} out, so {P3} saved."
    assert "tool values: {P1} {P2}" in p.notes
    assert not re.search(r"\d", re.sub(r"\{[PD]\d+\}", "", p.model_dump_json()))
    year = judge.payload("ask-net", "Your net for January 2026 was £2,407.66.")
    assert year.question == "Your net for January {D1} was {P1}."


def test_an_uncalibrated_judge_is_not_used() -> None:
    v = json.dumps(
        dict.fromkeys(("faithful", "clear", "tone", "refusal_correct"), True)
        | {"score": 3}
    )
    spy = Spy(v)
    out = judge.evaluate(CloudChatModel(bench.fake_key(), transport=spy))
    assert out["status"] == "uncalibrated"
    assert out["models"]["judge:gemini-pro-latest"]["metrics"]["kappa"] < 0.6
    assert "replies" not in out
    assert spy.calls == len(judge.labels()) == 30  # nothing beyond calibration


def test_a_malformed_verdict_is_no_verdict() -> None:
    assert judge.parse('{"score": 9}') is None
    assert judge.parse("five") is None
    assert judge.parse('{"score": 4, "faithful": true}') is None  # fields missing


def test_kappa() -> None:
    assert judge.kappa([1, 2, 3], [1, 2, 3]) == 1.0
    assert judge.kappa([5, 5, 1, 1], [5, 1, 5, 1]) == 0.0
    assert judge.kappa([5, 5, 5], [5, 5, 5]) == 1.0


def test_default_model_rule() -> None:
    def row(g: float, c: float) -> dict[str, Any]:
        return {"metrics": {"grounding_pass_rate": g, "cost_per_case_pence": c}}

    rows = {
        "cloud:a": row(0.9, 3.0),
        "cloud:b": row(0.95, 9.0),
        "cloud:c": row(0.95, 2.0),
    }
    assert bench.default_model(rows) == "c"


def test_the_live_run_never_runs_in_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "1")
    with pytest.raises(SystemExit):
        bench.live("gemini-flash-latest", yes=True)


def test_worst_case_stays_under_the_monthly_cap() -> None:
    calls, chat = bench.worst_case_pence("gemini-flash-latest")
    judged, pro = judge.worst_case_pence(calls)
    assert calls >= 23 and judged == 30 + calls
    assert chat + pro <= bench.MONTHLY_CAP_PENCE
