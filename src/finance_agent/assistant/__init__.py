"""The Assistant (docs/AI_DESIGN.md). ADR 0008: no LangSmith and no external
tracing, so tracing is forced off here, before langchain reads and caches the env."""

import os

os.environ.update(
    dict.fromkeys(
        (
            "LANGSMITH_TRACING",
            "LANGSMITH_TRACING_V2",
            "LANGCHAIN_TRACING",
            "LANGCHAIN_TRACING_V2",
        ),
        "false",
    )
)

import langsmith  # noqa: E402

langsmith.configure(enabled=False)

from finance_agent.assistant.chat_model import (  # noqa: E402
    ChatModel,
    FakeChatModel,
    Message,
    Reply,
    ToolCall,
    ToolSpec,
)
from finance_agent.assistant.ollama import (  # noqa: E402
    DEFAULT_MODEL,
    OllamaChatModel,
    OllamaError,
    OllamaHostRefused,
)

__all__ = [
    "DEFAULT_MODEL",
    "ChatModel",
    "FakeChatModel",
    "Message",
    "OllamaChatModel",
    "OllamaError",
    "OllamaHostRefused",
    "Reply",
    "ToolCall",
    "ToolSpec",
]
