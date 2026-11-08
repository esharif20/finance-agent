"""The chat endpoints (#139): one Assistant turn per POST, streamed as
server-sent events, for `current_user` only.

The reply streams only after the grounding check has passed (or the
deterministic table has replaced it), so no unchecked figure ever reaches the
screen. Events: `meta` (thread, which path answered, any confirm card, and a
cloud call's `cost_pence` and `payload_id`), then `delta` chunks of the reply,
then `done`. Bodies are parsed here rather than by FastAPI, so a refusal never
echoes the user's text (#169).

`cloud: true` opts the thread in to the privacy gateway (#301). The opt-in is
kept on the thread's checkpointed state, so it lasts for that conversation
only and ends when it is deleted or purged; it needs a Gemini key in the
Keychain, and the demo (#296) never has one."""

import json
import os
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Connection

from finance_agent.assistant import ChatModel, OllamaChatModel, OllamaError
from finance_agent.assistant.cloud_route import CloudRoute
from finance_agent.assistant.graph import Answer, Assistant, ask, resume, waiting_for
from finance_agent.assistant.ollama import DEFAULT_CHOICE, MODELS
from finance_agent.gateway.cloud import CloudChatModel, CloudError
from finance_agent.identity import current_user
from finance_agent.ledger.conversation import Conversations
from finance_agent.ledger.settings import CHOICES, Choice, Settings
from finance_agent.storage import keychain
from finance_agent.storage.spend import SpendStore

MESSAGE_MAX = 2000
IDLE = timedelta(days=1)  # a conversation lasts one session (DATA_POLICY)
_THREAD = re.compile(r"[0-9a-f]{32}")
_WORD = re.compile(r"\S+\s*|\s+")

router = APIRouter(prefix="/api/chat", tags=["chat"])


def model_for(conn: Connection) -> OllamaChatModel:
    """The model the user chose in Settings, or the default."""
    choice = Settings(conn, current_user()).assistant_model() or DEFAULT_CHOICE
    return OllamaChatModel(MODELS[choice])


def unload(model: str) -> None:
    """Ask Ollama to drop `model` now (an empty chat with keep_alive 0)."""
    try:
        OllamaChatModel(model, keep_alive="0").chat([])
    except (OllamaError, OSError):
        pass  # not loaded, or Ollama is off: it unloads itself when idle


def chat_model(request: Request) -> ChatModel:
    with request.app.state.engine.connect() as conn:
        return model_for(conn)


def today() -> date:
    return date.today()


def cloud_route(request: Request) -> CloudRoute | None:
    """The gateway, or None: in the demo, with no key, or with no ledger file
    for `spend.sqlite` to sit beside."""
    if os.environ.get("FA_DEMO"):
        return None
    db = getattr(getattr(request.app.state.engine, "url", None), "database", None)
    if not db or db == ":memory:":
        return None
    try:
        key = keychain.gemini_key()
        return CloudRoute(CloudChatModel(key or ""), SpendStore.beside(Path(db)))
    except CloudError:  # no key, or a malformed one
        return None
    except Exception:  # a locked or broken Keychain (the demo) fails closed
        return None


Model = Annotated[ChatModel, Depends(chat_model)]
Cloud = Annotated[CloudRoute | None, Depends(cloud_route)]
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
            "cost_pence": a.cost_pence,
            "payload_id": a.payload_id,
        }
        yield f"event: meta\ndata: {json.dumps(meta)}\n\n"
        for chunk in _WORD.findall(a.reply):
            yield f"event: delta\ndata: {json.dumps({'text': chunk})}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post("")
def chat(
    request: Request, body: Body, model: Model, day: Today, gateway: Cloud
) -> StreamingResponse:
    """One turn. Omit `thread_id` to start a conversation; the reply's `meta`
    event carries the new one. `cloud` (default false; omitted keeps the
    thread's choice) sends this conversation's wording through the gateway."""
    message = body.get("message")
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= MESSAGE_MAX:
        raise HTTPException(422, f"message is text of 1 to {MESSAGE_MAX} characters")
    if set(body) - {"message", "thread_id", "cloud"}:
        raise HTTPException(422, "only message, thread_id and cloud are accepted")
    cloud = body.get("cloud")
    if cloud is not None and not isinstance(cloud, bool):
        raise HTTPException(422, "cloud is true or false")
    if cloud and gateway is None:
        raise HTTPException(409, "the cloud gateway is not set up")
    with request.app.state.engine.begin() as conn:
        convo = Conversations(conn, current_user())
        convo.purge_idle(datetime.now(UTC) - IDLE)
        thread = body.get("thread_id")
        if thread is not None and not convo.has_thread(_thread(thread)):
            raise HTTPException(404, "no such conversation")
        assistant = Assistant(model, conn, day, cloud=gateway)
        answer = ask(assistant, message, thread, cloud)
    return _sse(answer)


@router.get("/model")
def get_model(request: Request) -> dict[str, Choice]:
    """Settings' Assistant model: "fast" (Faster) or "careful" (More careful)."""
    with request.app.state.engine.connect() as conn:
        return {
            "model": Settings(conn, current_user()).assistant_model() or DEFAULT_CHOICE
        }


@router.put("/model")
def set_model(request: Request, body: Body) -> dict[str, Choice]:
    """Change it; the old model is unloaded so two never sit in memory."""
    choice = body.get("model")
    if set(body) != {"model"} or choice not in CHOICES:
        raise HTTPException(422, "model is fast or careful")
    with request.app.state.engine.begin() as conn:
        s = Settings(conn, current_user())
        old = s.assistant_model() or DEFAULT_CHOICE
        s.set_assistant_model(choice)
    if old != choice:
        unload(MODELS[old])
    return {"model": choice}


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
