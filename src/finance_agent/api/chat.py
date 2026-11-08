"""The chat endpoints (#139): one Assistant turn per POST, streamed as
server-sent events, for `current_user` only.

The reply streams only after the grounding check has passed (or the
deterministic table has replaced it), so no unchecked figure ever reaches the
screen. Events: `stage` (progress words while the turn runs, never data),
`meta` (thread, which path answered, any confirm card, the Transactions
behind each spending total, and a cloud call's `cost_pence` and
`payload_id`), then `delta` chunks of the reply, then `done`; `error` if the
turn failed. Bodies are parsed here rather than by FastAPI, so a refusal never
echoes the user's text (#169).

`cloud: true` opts the thread in to the privacy gateway (#301). The opt-in is
kept on the thread's checkpointed state, so it lasts for that conversation
only and ends when it is deleted or purged; it needs a Gemini key in the
Keychain, and the demo (#296) never has one."""

import contextvars
import json
import os
import queue
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import Connection, Engine

from finance_agent.assistant import ChatModel, OllamaChatModel, OllamaError
from finance_agent.assistant.cloud_route import CloudRoute
from finance_agent.assistant.graph import Answer, Assistant, ask, resume, waiting_for
from finance_agent.assistant.ollama import DEFAULT_CHOICE, MODELS, WARM_KEEP_ALIVE
from finance_agent.gateway.cloud import CloudChatModel, CloudError, Usage
from finance_agent.gateway.types import GatewayPayload
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


class LazyCloud:
    """The cloud model, keyed on its first call: the Keychain is read only
    when a thread actually words a reply in the cloud, at most once per
    process (`keychain.cached_gemini_key`), never per request or at startup."""

    _model: CloudChatModel | None = None  # shared by the process's requests

    def complete(
        self, payload: GatewayPayload, *, model: str, max_tokens: int
    ) -> tuple[str, Usage]:
        if LazyCloud._model is None:
            try:
                key = keychain.cached_gemini_key()
            except Exception:  # a locked or broken Keychain fails closed
                raise CloudError("the Keychain could not be read") from None
            LazyCloud._model = CloudChatModel(key or "")  # CloudError when none
        return LazyCloud._model.complete(payload, model=model, max_tokens=max_tokens)


def cloud_route(request: Request) -> CloudRoute | None:
    """The gateway, or None: in the demo, when no key was stored (the
    non-secret flag, not the Keychain), or with no ledger file for
    `spend.sqlite` to sit beside."""
    if os.environ.get("FA_DEMO"):
        return None
    db = getattr(getattr(request.app.state.engine, "url", None), "database", None)
    if not db or db == ":memory:" or not keychain.gemini_configured(Path(db)):
        return None
    return CloudRoute(LazyCloud(), SpendStore.beside(Path(db)))


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


def _meta(a: Answer) -> dict[str, Any]:
    return {
        "thread_id": a.thread_id,
        "turn_id": a.turn_id,
        "route": a.route,
        "path": a.path,
        "evidence": list(a.evidence),
        "proposal_id": a.proposal_id,
        "cost_pence": a.cost_pence,
        "payload_id": a.payload_id,
        "resolved": list(a.resolved),
        "chips": list(a.chips),
        "timing": a.timing,
        "counted": [
            {
                "evidence_id": e.evidence_id,
                "total_minor": e.total_minor,
                "transactions": [
                    {
                        "id": t.transaction_id,
                        "posted_on": t.posted_on.isoformat(),
                        "merchant": t.merchant,
                        "amount_minor": t.amount_minor,
                    }
                    for t in e.transactions
                ],
            }
            for e in a.counted
        ],
    }


def _event(name: str, data: object) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def _sse(
    engine: Engine,
    work: Callable[[Assistant], Answer],
    make: Callable[[Connection], Assistant],
) -> StreamingResponse:
    """Run the turn on its own thread and stream its stages, then the reply.
    The turn finishes and commits even if the client goes away, so a
    reloaded screen can still find the conversation."""
    q: queue.Queue[tuple[str, object]] = queue.Queue()

    def run() -> None:
        try:
            with engine.begin() as conn:
                assistant = make(conn)
                assistant.on_stage = lambda words: q.put(("stage", words))
                q.put(("answer", work(assistant)))
        # anything at all, so the stream never waits forever; its text could
        # quote data, so it is never logged
        except BaseException:  # noqa: BLE001
            q.put(("error", None))

    # the request's context goes along: the demo's per-visitor ledger is in it
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(run,), daemon=True).start()

    def events() -> Iterator[str]:
        while True:
            kind, item = q.get()
            if kind == "stage":
                yield _event("stage", {"text": item})
            elif kind == "error":
                yield _event("error", {})
                return
            elif isinstance(item, Answer):
                yield _event("meta", _meta(item))
                for chunk in _WORD.findall(item.reply):
                    yield _event("delta", {"text": chunk})
                yield _event("done", {})
                return

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
    engine: Engine = request.app.state.engine
    with engine.begin() as conn:
        convo = Conversations(conn, current_user())
        convo.purge_idle(datetime.now(UTC) - IDLE)
        thread = body.get("thread_id")
        if thread is not None and not convo.has_thread(_thread(thread)):
            raise HTTPException(404, "no such conversation")
    return _sse(
        engine,
        lambda a: ask(a, message, thread, cloud),
        lambda conn: Assistant(model, conn, day, cloud=gateway, prefetch=True),
    )


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
    # the turns' options (same num_ctx, so no reload); WARM_KEEP_ALIVE until
    # the first turn shortens it to KEEP_ALIVE
    if isinstance(model, OllamaChatModel):
        model = OllamaChatModel(model.model, keep_alive=WARM_KEEP_ALIVE)
    try:
        model.chat([])
    except (OllamaError, OSError):
        return {"warm": False}
    return {"warm": True}


@router.post("/cool")
def cool(request: Request) -> dict[str, bool]:
    """The chat screen closed: unload the model now rather than in 30 minutes."""
    with request.app.state.engine.connect() as conn:
        choice = Settings(conn, current_user()).assistant_model() or DEFAULT_CHOICE
    unload(MODELS[choice])
    return {"cool": True}


@router.post("/{thread_id}/resume")
def decide(
    request: Request, thread_id: str, body: Body, model: Model, day: Today
) -> StreamingResponse:
    """Answer the confirm card the thread is paused on."""
    decision: Literal["confirm", "reject"] | Any = body.get("decision")
    if decision not in ("confirm", "reject") or set(body) != {"decision"}:
        raise HTTPException(422, "decision is confirm or reject")
    thread = _thread(thread_id)
    engine: Engine = request.app.state.engine
    with engine.begin() as conn:
        if not Conversations(conn, current_user()).has_thread(thread):
            raise HTTPException(404, "no such conversation")
        if waiting_for(Assistant(model, conn, day), thread) is None:
            raise HTTPException(409, "nothing is waiting for confirmation")
    return _sse(
        engine,
        lambda a: resume(a, thread, decision),
        lambda conn: Assistant(model, conn, day),
    )


@router.delete("/{thread_id}")
def end(request: Request, thread_id: str) -> dict[str, int]:
    """End the conversation: its checkpoints are deleted."""
    thread = _thread(thread_id)
    with request.app.state.engine.begin() as conn:
        n = Conversations(conn, current_user()).delete_thread(thread)
    if not n:
        raise HTTPException(404, "no such conversation")
    return {"deleted": n}
