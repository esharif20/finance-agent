"""The chat endpoints (#139): one Assistant turn per POST, streamed as
server-sent events, for `current_user` only.

The reply streams only after the grounding check has passed (or the
deterministic table has replaced it), so no unchecked figure ever reaches the
screen. Events: `meta` (thread, which path answered, any confirm card), then
`delta` chunks of the reply, then `done`. Bodies are parsed here rather than
by FastAPI, so a refusal never echoes the user's text (#169)."""

import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from finance_agent.assistant import ChatModel, OllamaChatModel, OllamaError
from finance_agent.assistant.graph import Answer, Assistant, ask, resume, waiting_for
from finance_agent.identity import current_user
from finance_agent.ledger.conversation import Conversations

MESSAGE_MAX = 2000
IDLE = timedelta(days=1)  # a conversation lasts one session (DATA_POLICY)
_THREAD = re.compile(r"[0-9a-f]{32}")
_WORD = re.compile(r"\S+\s*|\s+")

router = APIRouter(prefix="/api/chat", tags=["chat"])


def chat_model() -> ChatModel:
    return OllamaChatModel()


def today() -> date:
    return date.today()


Model = Annotated[ChatModel, Depends(chat_model)]
Today = Annotated[date, Depends(today)]


async def _body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "the body is not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(422, "the body is a JSON object")
    return body  # pyright: ignore[reportUnknownVariableType]


Body = Annotated[dict[str, Any], Depends(_body)]


def _thread(value: object) -> str:
    if not isinstance(value, str) or not _THREAD.fullmatch(value):
        raise HTTPException(404, "no such conversation")
    return value


def _sse(a: Answer) -> StreamingResponse:
    def events() -> Iterator[str]:
        meta = {
            "thread_id": a.thread_id,
            "turn_id": a.turn_id,
            "route": a.route,
            "path": a.path,
            "evidence": list(a.evidence),
            "proposal_id": a.proposal_id,
        }
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        for chunk in _WORD.findall(a.reply):
            yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("")
def chat(request: Request, body: Body, model: Model, day: Today) -> StreamingResponse:
    """One turn. Omit `thread_id` to start a conversation; the reply's `meta`
    event carries the new one."""
    message = body.get("message")
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= MESSAGE_MAX:
        raise HTTPException(422, f"message is text of 1 to {MESSAGE_MAX} characters")
    if set(body) - {"message", "thread_id"}:
        raise HTTPException(422, "only message and thread_id are accepted")
    with request.app.state.engine.begin() as conn:
        convo = Conversations(conn, current_user())
        convo.purge_idle(datetime.now(UTC) - IDLE)
        thread = body.get("thread_id")
        if thread is not None and not convo.has_thread(_thread(thread)):
            raise HTTPException(404, "no such conversation")
        answer = ask(Assistant(model, conn, day), message, thread)
    return _sse(answer)


@router.post("/warm")
def warm(model: Model) -> dict[str, bool]:
    """Load the model while the chat screen opens, so the first turn doesn't
    wait for it. An empty chat only loads; it stays for the keep_alive."""
    try:
        model.chat([])
    except (OllamaError, OSError):
        return {"warm": False}
    return {"warm": True}


@router.post("/{thread_id}/resume")
def decide(
    request: Request, thread_id: str, body: Body, model: Model, day: Today
) -> StreamingResponse:
    """Answer the confirm card the thread is paused on."""
    decision: Literal["confirm", "reject"] | Any = body.get("decision")
    if decision not in ("confirm", "reject") or set(body) != {"decision"}:
        raise HTTPException(422, "decision is confirm or reject")
    thread = _thread(thread_id)
    with request.app.state.engine.begin() as conn:
        if not Conversations(conn, current_user()).has_thread(thread):
            raise HTTPException(404, "no such conversation")
        assistant = Assistant(model, conn, day)
        if waiting_for(assistant, thread) is None:
            raise HTTPException(409, "nothing is waiting for confirmation")
        answer = resume(assistant, thread, decision)
    return _sse(answer)


@router.delete("/{thread_id}")
def end(request: Request, thread_id: str) -> dict[str, int]:
    """End the conversation: its checkpoints are deleted."""
    thread = _thread(thread_id)
    with request.app.state.engine.begin() as conn:
        n = Conversations(conn, current_user()).delete_thread(thread)
    if not n:
        raise HTTPException(404, "no such conversation")
    return {"deleted": n}
