"""`OllamaChatModel`: the local model over HTTP to localhost:11434 only (#135,
THREAT_MODEL B4). `http.client` rather than a new client: it follows no
redirects and reads no proxy env vars, so the host it dials is the one checked here.
Errors never echo prompt or reply text."""

import http.client
import json
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from finance_agent.assistant.chat_model import Message, Reply, ToolCall, ToolSpec

OLLAMA_URL = "http://localhost:11434"
OLLAMA_PORT = 11434
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
DEFAULT_MODEL = (
    "qwen3.5:4b"  # light footprint; the #143/#144 benchmark picks the final one
)
KEEP_ALIVE = "2m"


class OllamaHostRefused(ValueError):
    pass


class OllamaError(RuntimeError):
    pass


class _Response(Protocol):
    status: int

    def read(self) -> bytes: ...
    def getheader(self, name: str) -> str | None: ...
    def __iter__(self) -> Iterator[bytes]: ...
    def close(self) -> None: ...


class _Connection(Protocol):
    def request(
        self, method: str, url: str, body: bytes, headers: Mapping[str, str]
    ) -> None: ...
    def getresponse(self) -> _Response: ...
    def close(self) -> None: ...


# Ollama's tool-call wire shape. Anything else in it is refused (#135).
class _WireFunction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    index: int | None = None
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]


class _WireToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str | None = None
    type: Literal["function"] | None = None
    function: _WireFunction


_TOOL_CALLS = TypeAdapter(list[_WireToolCall])


def _local_endpoint(base_url: str) -> tuple[str, int]:
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:  # e.g. a malformed IPv6 literal or port
        raise OllamaHostRefused(
            "the assistant may only talk to http://localhost:11434"
        ) from None
    if (
        parts.scheme != "http"
        or parts.hostname not in LOCAL_HOSTS
        or port != OLLAMA_PORT
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise OllamaHostRefused("the assistant may only talk to http://localhost:11434")
    return parts.hostname, port


def _wire(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {"function": {"name": t.name, "arguments": t.arguments}}
            for t in m.tool_calls
        ]
    if m.tool_name is not None:
        out["tool_name"] = m.tool_name
    return out


def _reply(chunk: Any) -> Reply:
    if not isinstance(chunk, dict) or "error" in chunk:
        raise OllamaError("Ollama reported an error")
    message: Any = cast(dict[str, Any], chunk).get("message") or {}
    if not isinstance(message, dict):
        raise OllamaError("malformed reply from Ollama")
    message = cast(dict[str, Any], message)
    content = message.get("content") or ""
    if not isinstance(content, str):
        raise OllamaError("malformed reply from Ollama")
    try:
        calls = _TOOL_CALLS.validate_python(message.get("tool_calls") or [])
    except ValidationError:
        raise OllamaError("malformed tool call from the model") from None
    return Reply(
        content=content,
        tool_calls=tuple(
            ToolCall(name=c.function.name, arguments=c.function.arguments)
            for c in calls
        ),
    )


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        raise OllamaError("malformed reply from Ollama") from None


class OllamaChatModel:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = OLLAMA_URL,
        *,
        keep_alive: str = KEEP_ALIVE,
        timeout: float = 120.0,
        connect: Callable[[], _Connection] | None = None,
    ) -> None:
        self.model = model
        self._host, self._port = _local_endpoint(base_url)
        self._keep_alive = keep_alive
        self._timeout = timeout
        self._factory: Callable[[], _Connection] = connect or self.open_connection

    def open_connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    @contextmanager
    def _post(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec], stream: bool
    ) -> Generator[_Response]:
        body = {
            "model": self.model,
            "messages": [_wire(m) for m in messages],
            "stream": stream,
            "keep_alive": self._keep_alive,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
        conn = self._factory()
        try:
            try:
                conn.request(
                    "POST",
                    "/api/chat",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
            except (OSError, http.client.HTTPException):
                raise OllamaError("could not reach Ollama on localhost:11434") from None
            if 300 <= resp.status < 400:
                raise OllamaError(f"Ollama redirect refused (HTTP {resp.status})")
            if resp.status != 200:
                raise OllamaError(f"Ollama returned HTTP {resp.status}")
            yield resp
        finally:
            conn.close()

    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        with self._post(messages, tools, stream=False) as resp:
            return _reply(_json(resp.read()))

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Iterator[Reply]:
        with self._post(messages, tools, stream=True) as resp:
            lines = iter(resp)
            while True:
                try:
                    line = next(lines, None)
                except (OSError, http.client.HTTPException):
                    raise OllamaError("the Ollama stream broke off") from None
                if line is None:
                    return
                if not line.strip():
                    continue
                chunk = _json(line)
                yield _reply(chunk)
                if cast(dict[str, Any], chunk).get("done"):
                    return
