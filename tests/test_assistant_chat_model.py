"""#135: the `ChatModel` seam. `FakeChatModel` is what graph tests and CI run on;
`OllamaChatModel` is tested through a stand-in connection, so CI never needs Ollama.
Synthetic text only."""

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from finance_agent.assistant import (
    DEFAULT_MODEL,
    ChatModel,
    FakeChatModel,
    Message,
    OllamaChatModel,
    OllamaError,
    OllamaHostRefused,
    Reply,
    ToolCall,
    ToolSpec,
)

SPEND = ToolSpec(
    name="spending_summary",
    description="Spending by category for a month",
    parameters={
        "type": "object",
        "properties": {"month": {"type": "string"}},
        "required": ["month"],
    },
)
ASK = [Message(role="user", content="What did I spend on eating out in May?")]


# --- FakeChatModel -----------------------------------------------------------


def test_fake_replays_its_script_in_order_and_records_calls() -> None:
    call = ToolCall(name="spending_summary", arguments={"month": "2026-05"})
    fake = FakeChatModel([Reply(tool_calls=(call,)), Reply(content="You spent it.")])
    model: ChatModel = fake

    assert model.chat(ASK, [SPEND]) == Reply(tool_calls=(call,))
    assert model.chat(ASK) == Reply(content="You spent it.")
    assert fake.calls == [(tuple(ASK), (SPEND,)), (tuple(ASK), ())]


def test_fake_raises_when_the_script_runs_out() -> None:
    with pytest.raises(AssertionError, match="script"):
        FakeChatModel([]).chat(ASK)


def test_fake_stream_is_deterministic_and_reassembles_to_the_reply() -> None:
    call = ToolCall(name="spending_summary", arguments={"month": "2026-05"})
    reply = Reply(content="Eating out was the top category.", tool_calls=(call,))
    chunks = list(FakeChatModel([reply]).stream(ASK))
    again = list(FakeChatModel([reply]).stream(ASK))

    assert chunks == again
    assert len(chunks) > 2
    assert "".join(c.content for c in chunks) == reply.content
    assert tuple(t for c in chunks for t in c.tool_calls) == (call,)


def test_messages_and_tool_calls_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ToolCall.model_validate({"name": "x", "arguments": {}, "run": "rm -rf"})
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "user", "content": "hi", "sudo": True})
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "root", "content": "hi"})


# --- OllamaChatModel: host pinning -------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://localhost:11434/",
        "http://127.0.0.1:11434",
        "http://[::1]:11434",
    ],
)
def test_ollama_accepts_only_local_11434(url: str) -> None:
    OllamaChatModel(base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:11434",  # another host
        "http://localhost.example.com:11434",  # looks local, is not
        "http://localhost:8080",  # another port
        "http://localhost",  # default port 80
        "https://localhost:11434",  # Ollama serves plain http
        "http://user:pw@localhost:11434",  # credentials smuggled in
        "http://evil.example@localhost:11434",
        "http://localhost:11434@evil.example",
        "http://localhost:11434/proxy/http://evil.example",  # a path is not allowed
        "http://192.0.2.1:11434",
        "http://0.0.0.0:11434",
        "file:///etc/passwd",
        "localhost:11434",
        # verifier (#164): more look-alikes and a malformed IPv6 literal
        "http://[::ffff:127.0.0.1]:11434",
        "http://localhost.:11434",
        "http://127.1:11434",
        "http://2130706433:11434",
        "http://[::1:11434]",
        "http://localhost:11434\\@evil.example",
        "http://localhost:+11434",
    ],
)
def test_ollama_refuses_any_other_base_url(url: str) -> None:
    with pytest.raises(OllamaHostRefused):
        OllamaChatModel(base_url=url)


# --- OllamaChatModel: the wire, through a stand-in connection -----------------


class FakeResponse:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self._body = body
        self._headers = headers

    def read(self) -> bytes:
        return self._body

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)

    def __iter__(self) -> Iterator[bytes]:
        return iter(self._body.splitlines(keepends=True))

    def close(self) -> None:
        pass


class FakeConnection:
    """Records each request; answers with a canned response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, body: bytes, headers: Any) -> None:
        self.requests.append((method, url, json.loads(body)))

    def getresponse(self) -> FakeResponse:
        return self.response

    def close(self) -> None:
        pass


def ollama_with(
    status: int = 200, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[OllamaChatModel, list[FakeConnection]]:
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    opened: list[FakeConnection] = []

    def connect() -> FakeConnection:
        opened.append(FakeConnection(FakeResponse(status, raw, headers or {})))
        return opened[-1]

    return OllamaChatModel(connect=connect), opened


def wire_reply(message: dict[str, Any]) -> dict[str, Any]:
    return {"model": DEFAULT_MODEL, "done": True, "message": message}


def test_default_model_and_keep_alive() -> None:
    assert DEFAULT_MODEL == "qwen3.5:4b"
    model, opened = ollama_with(body=wire_reply({"role": "assistant", "content": "ok"}))
    model.chat(ASK, [SPEND])
    method, path, sent = opened[0].requests[0]
    assert (method, path) == ("POST", "/api/chat")
    assert sent["model"] == "qwen3.5:4b"
    assert sent["keep_alive"] == "2m"
    assert sent["stream"] is False
    assert sent["messages"] == [
        {"role": "user", "content": "What did I spend on eating out in May?"}
    ]
    assert sent["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "spending_summary",
                "description": "Spending by category for a month",
                "parameters": SPEND.parameters,
            },
        }
    ]


def test_ollama_parses_content_and_tool_calls() -> None:
    model, _ = ollama_with(
        body=wire_reply(
            {
                "role": "assistant",
                "content": "",
                "thinking": "the user wants May",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {
                            "index": 0,
                            "name": "spending_summary",
                            "arguments": {"month": "2026-05"},
                        },
                    }
                ],
            }
        )
    )
    assert model.chat(ASK, [SPEND]) == Reply(
        tool_calls=(ToolCall(name="spending_summary", arguments={"month": "2026-05"}),)
    )


def test_tool_results_go_back_with_their_tool_name() -> None:
    model, opened = ollama_with(body=wire_reply({"role": "assistant", "content": "ok"}))
    call = ToolCall(name="spending_summary", arguments={"month": "2026-05"})
    model.chat(
        [
            *ASK,
            Message(role="assistant", tool_calls=(call,)),
            Message(role="tool", tool_name="spending_summary", content='{"rows": []}'),
        ]
    )
    sent = opened[0].requests[0][2]["messages"]
    assert sent[1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "spending_summary",
                    "arguments": {"month": "2026-05"},
                }
            }
        ],
    }
    assert sent[2] == {
        "role": "tool",
        "content": '{"rows": []}',
        "tool_name": "spending_summary",
    }


@pytest.mark.parametrize(
    "tool_call",
    [
        # an unexpected field beside the function
        {"function": {"name": "x", "arguments": {}}, "execute": True},
        # an unexpected field inside the function
        {"function": {"name": "x", "arguments": {}, "write": "ledger"}},
        # the qwen printing bug: arguments as a string
        {"function": {"name": "x", "arguments": '{"month": "2026-05"}'}},
        {"function": {"name": "", "arguments": {}}},
        {"function": {"arguments": {}}},
        {"name": "x", "arguments": {}},
    ],
)
def test_adversarial_malformed_tool_call_payload_is_rejected(
    tool_call: dict[str, Any],
) -> None:
    model, _ = ollama_with(
        body=wire_reply({"role": "assistant", "content": "", "tool_calls": [tool_call]})
    )
    with pytest.raises(OllamaError):
        model.chat(ASK, [SPEND])


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_adversarial_redirect_to_an_external_host_is_refused(status: int) -> None:
    model, opened = ollama_with(
        status=status, body=b"", headers={"Location": "http://evil.example/api/chat"}
    )
    with pytest.raises(OllamaError, match="redirect"):
        model.chat(ASK)
    with pytest.raises(OllamaError, match="redirect"):
        list(model.stream(ASK))
    assert len(opened) == 2  # one connection per call, none to the Location


def test_http_errors_raise_without_echoing_the_body() -> None:
    model, _ = ollama_with(status=500, body=b"secret prompt text")
    with pytest.raises(OllamaError) as e:
        model.chat(ASK)
    assert "secret" not in str(e.value)


def test_ollama_error_field_raises() -> None:
    model, _ = ollama_with(body={"error": "model not found"})
    with pytest.raises(OllamaError):
        model.chat(ASK)


def test_ollama_stream_yields_deltas_then_tool_calls() -> None:
    lines: list[dict[str, Any]] = [
        {"message": {"role": "assistant", "content": "Eating "}, "done": False},
        {"message": {"role": "assistant", "content": "out."}, "done": False},
        {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "spending_summary", "arguments": {}}}
                ],
            },
            "done": False,
        },
        {"message": {"role": "assistant", "content": ""}, "done": True},
    ]
    body = b"".join(json.dumps(x).encode() + b"\n" for x in lines)
    model, opened = ollama_with(body=body)
    chunks = list(model.stream(ASK, [SPEND]))
    assert opened[0].requests[0][2]["stream"] is True
    assert "".join(c.content for c in chunks) == "Eating out."
    assert [t for c in chunks for t in c.tool_calls] == [
        ToolCall(name="spending_summary", arguments={})
    ]


def test_ollama_stream_rejects_a_malformed_tool_call() -> None:
    line: dict[str, Any] = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "x", "arguments": {}, "y": 1}}],
        },
        "done": True,
    }
    model, _ = ollama_with(body=json.dumps(line).encode())
    with pytest.raises(OllamaError):
        list(model.stream(ASK))


def test_a_socket_error_mid_stream_is_an_ollama_error() -> None:
    class Dropped(FakeResponse):
        def __iter__(self) -> Iterator[bytes]:
            yield b'{"message": {"content": "Eating "}, "done": false}\n'
            raise TimeoutError("synthetic drop")

    model = OllamaChatModel(connect=lambda: FakeConnection(Dropped(200, b"", {})))
    with pytest.raises(OllamaError):
        list(model.stream(ASK))


# --- the default connection: proxies cannot divert it -------------------------


def test_adversarial_proxy_env_vars_cannot_divert_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(var, "http://evil.example:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    conn = OllamaChatModel(base_url="http://127.0.0.1:11434").open_connection()
    assert (conn.host, conn.port) == ("127.0.0.1", 11434)
    assert vars(conn)["_tunnel_host"] is None  # no CONNECT tunnel to a proxy


# --- opt-in: the real local Ollama --------------------------------------------


@pytest.mark.real_ollama
@pytest.mark.skipif(
    os.environ.get("FA_REAL_OLLAMA") != "1", reason="set FA_REAL_OLLAMA=1 to run"
)
def test_real_ollama_answers_and_calls_a_tool() -> None:
    model = OllamaChatModel()
    reply = model.chat(
        [
            Message(
                role="system",
                content="Call the spending_summary tool. Do not answer in prose.",
            ),
            Message(role="user", content="How much did I spend in 2026-05?"),
        ],
        [SPEND],
    )
    assert [t.name for t in reply.tool_calls] == ["spending_summary"]
    streamed = list(model.stream([Message(role="user", content="Say hello.")]))
    assert "".join(c.content for c in streamed).strip()
