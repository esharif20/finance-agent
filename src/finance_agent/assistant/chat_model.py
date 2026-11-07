"""The `ChatModel` seam (#135, docs/AI_DESIGN.md): typed messages and tool calls,
and the scripted `FakeChatModel` that graph tests and CI run on."""

import re
from collections.abc import Iterator, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

_STRICT = ConfigDict(extra="forbid", frozen=True)


class ToolCall(BaseModel):
    model_config = _STRICT
    name: str = Field(min_length=1)
    arguments: dict[str, JsonValue]


class ToolSpec(BaseModel):
    """A tool offered to the model; `parameters` is its JSON Schema."""

    model_config = _STRICT
    name: str = Field(min_length=1)
    description: str
    parameters: dict[str, JsonValue]


class Message(BaseModel):
    model_config = _STRICT
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_name: str | None = None  # on a "tool" message: which tool's result it is


class Reply(BaseModel):
    """A model reply; from `stream`, one delta of it."""

    model_config = _STRICT
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()


class ChatModel(Protocol):
    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply: ...

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Iterator[Reply]: ...


class FakeChatModel:
    """Replays a script of replies, one per call, and records what it was sent."""

    def __init__(self, script: Sequence[Reply]) -> None:
        self._script = list(script)
        self.calls: list[tuple[tuple[Message, ...], tuple[ToolSpec, ...]]] = []

    def chat(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        self.calls.append((tuple(messages), tuple(tools)))
        if not self._script:
            raise AssertionError("FakeChatModel script ran out of replies")
        return self._script.pop(0)

    def stream(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> Iterator[Reply]:
        reply = self.chat(messages, tools)
        for word in re.findall(r"\S+\s*|\s+", reply.content):
            yield Reply(content=word)
        if reply.tool_calls:
            yield Reply(tool_calls=reply.tool_calls)
