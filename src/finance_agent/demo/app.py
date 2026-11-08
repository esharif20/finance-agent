"""The public demo (ADR 0016): `uvicorn finance_agent.demo.app:app`. It reuses
the API's routers over per-visitor synthetic ledgers, with every route that
reaches beyond the visitor's own file (card art, sync, import, the local
model, backups, the gateway) answering 404: only ALLOWED paths pass `Guard`."""

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import Engine
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection
from starlette.responses import FileResponse, PlainTextResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from finance_agent.api import chat, create_app, search
from finance_agent.assistant import ChatModel, context
from finance_agent.assistant.chat_model import Message as ChatMessage
from finance_agent.assistant.chat_model import Reply, ToolCall, ToolSpec
from finance_agent.demo import guard, session
from finance_agent.ledger.taxonomy import TAXONOMY

BODY_LIMIT = 64 * 1024
SAFE = {"GET", "HEAD", "OPTIONS"}
# Fail closed: a route the API gains later stays off here until listed.
ALLOWED = (
    "/api/health",
    "/api/summary",
    "/api/spending",
    "/api/categories",
    "/api/months",
    "/api/month-series",
    "/api/accounts",
    "/api/transactions",
    "/api/corrections",
    "/api/subscriptions",
    "/api/review-queue",
    "/api/splits",
    "/api/notes",
    "/api/merchants",
    "/api/fx",
    "/api/manage-links",
    "/api/proposals",
    "/api/memories",
    "/api/feedback",
    "/api/chat",
    "/api/cash-flow",
    "/api/insights",
    "/api/briefing",
    "/api/goals",  # M6: read-only GETs over the visitor's own ledger
    "/api/guidance",
    "/api/plan",
    "/api/search",
    "/api/demo",
)
OFF = {"/api/chat/model", "/api/chat/warm"}  # they call Ollama
SCHEMA = ("/openapi.json", "/docs", "/redoc")


def allowed(path: str) -> bool:
    """Under /api only ALLOWED; elsewhere the web build, not the schema."""
    if not path.startswith("/api"):
        return path not in SCHEMA
    return path not in OFF and any(
        path == p or path.startswith(p + "/") for p in ALLOWED
    )


def _leaves() -> list[str]:
    """Every Category name a question may name, longest first ("AI tools"
    before "Software"), Subcategories before their top level."""
    subs = {s for tops in TAXONOMY.values() for s in tops} - set(TAXONOMY)
    return sorted(subs, key=len, reverse=True) + sorted(TAXONOMY, key=len, reverse=True)


_NAMES = _leaves()


def recorded_call(message: str, today: date) -> ToolCall:
    """The read tool a question maps to, chosen by keywords: the recorded
    stand-in for the model's tool choice. It names a tool and its arguments,
    never a figure; every figure comes from the tool (ADR 0007)."""
    text = message.lower()
    month = None
    if "last month" in text:
        prev = today.replace(day=1) - timedelta(days=1)
        month = f"{prev:%Y-%m}"
    if re.search(r"subscription|recurring", text):
        return ToolCall(name="list_subscriptions", arguments={})
    if re.search(r"insight|unusual|notice|stand out", text):
        return ToolCall(name="list_insights", arguments={})
    for name in _NAMES:
        singular = name.lower().removesuffix("s")
        if re.search(rf"\b{re.escape(singular)}", text):
            tool = "category_trend" if "trend" in text and name in TAXONOMY else None
            return ToolCall(
                name=tool or "category_detail",
                arguments={"category": name, "month": month},
            )
    return ToolCall(name="spending_summary", arguments={"month": month})


class RecordedModel:
    """The demo's ChatModel, with no model behind it: the Router hears "ask",
    the first tool round gets `recorded_call`, and the next round fails, so
    the Assistant answers with its deterministic table of tool results."""

    def chat(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec] = ()
    ) -> Reply:
        offered = {t.name for t in tools}
        if not offered:
            if messages and messages[0].content == context.SYSTEM["router"]:
                return Reply(content="ask")
            raise OSError("the demo has no chat model")
        if messages[-1].role != "user":  # tool results are in: no wording
            raise OSError("the demo has no chat model")
        call = recorded_call(messages[-1].content, date.today())
        if call.name not in offered:
            raise OSError("the demo has no chat model")
        return Reply(tool_calls=(call,))

    def stream(self, *_: Any, **__: Any) -> Any:
        raise OSError("the demo has no chat model")


def _recorded_model() -> ChatModel:
    return RecordedModel()


@dataclass
class Visit:
    visitor: session.Visitor
    fresh: bool  # send a Set-Cookie with the response


class PersonaOut(BaseModel):
    persona: int


async def _refuse(scope: Scope, receive: Receive, send: Send, status: int) -> None:
    text = {403: "Cross-origin write refused.", 404: "Not Found"}.get(status, "")
    await PlainTextResponse(text or "Request body too large.", status)(
        scope, receive, send
    )


def same_origin(headers: Headers) -> bool:
    origin, host = headers.get("origin"), headers.get("host")
    if not origin or not host:
        return False
    try:
        u = urlsplit(origin)
    except ValueError:
        return False
    return u.scheme in ("https", "http") and u.netloc == host and not u.path


def _route_path(scope: Scope) -> str:
    """As Starlette's router sees it: the path less a matching root_path."""
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if root and path.startswith(root) and path[len(root) : len(root) + 1] in ("/", ""):
        return path[len(root) :]
    return path


class Guard:
    """Per request: 404 for paths the demo lacks, 403 for a write from another
    Origin, 413 for a body over 64 KB, then the visitor's ledger bound as the
    engine and a fresh signed cookie for anyone without a valid one."""

    def __init__(self, app: ASGIApp, secret: bytes, visitors: session.Visitors):
        self.app, self.secret, self.visitors = app, secret, visitors

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # the router matches the path less root_path: both must be allowed
        if not (allowed(scope["path"]) and allowed(_route_path(scope))):
            return await _refuse(scope, receive, send, 404)
        headers = Headers(scope=scope)
        if scope["method"] not in SAFE and not same_origin(headers):
            return await _refuse(scope, receive, send, 403)
        body = await _read(receive, headers)
        if body is None:
            return await _refuse(scope, receive, send, 413)

        n = len(self.visitors.personas)
        cookie = HTTPConnection(scope).cookies.get(session.COOKIE)
        visitor = session.verify(self.secret, cookie, n)
        state = Visit(visitor or session.new_visitor(n), fresh=visitor is None)
        scope["fa_demo"] = state
        db = self.visitors.open(state.visitor)

        async def replay() -> Message:
            nonlocal body
            if body is None:
                return await receive()
            chunk, body = body, None
            return {"type": "http.request", "body": chunk, "more_body": False}

        async def send_cookie(message: Message) -> None:
            if message["type"] == "http.response.start" and state.fresh:
                value = session.sign(self.secret, state.visitor)
                cookie = (
                    f"{session.COOKIE}={value}; Max-Age={session.MAX_AGE}; Path=/; "
                    "HttpOnly; Secure; SameSite=Lax"
                )
                message["headers"] = [
                    *message.get("headers", []),
                    (b"set-cookie", cookie.encode()),
                ]
            await send(message)

        with session.bound(db):
            await self.app(scope, replay, send_cookie)


async def _read(receive: Receive, headers: Headers) -> bytes | None:
    """The whole body, or None once it passes BODY_LIMIT."""
    declared = headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > BODY_LIMIT):
        return None
    chunks: list[bytes] = []
    size = 0
    while True:
        m = await receive()
        if m["type"] != "http.request":
            break
        chunk: bytes = m.get("body", b"")
        size += len(chunk)
        if size > BODY_LIMIT:
            return None
        chunks.append(chunk)
        if not m.get("more_body"):
            break
    return b"".join(chunks)


def build(environ: Mapping[str, str] = os.environ) -> FastAPI:
    cfg = guard.config(environ)
    guard.lock_keychain()
    guard.seed(cfg)
    visitors = session.Visitors(cfg.dir)
    if [p.stem for p in visitors.personas] != [
        str(i) for i in range(len(visitors.personas))
    ] or not visitors.personas:
        raise guard.Refused("no personas: run scripts/build_demo_db.py first")

    app = create_app(cast(Engine, session.VisitorEngine()), web_dir=cfg.dir / "web")
    app.dependency_overrides[chat.chat_model] = _recorded_model
    app.dependency_overrides[search.embedder] = lambda: None  # FTS only: no Ollama

    def visitor(request: Request) -> session.Visitor:
        return request.scope["fa_demo"].visitor

    @app.post("/api/demo/reset")
    def reset(request: Request) -> PersonaOut:
        """Back to the Persona as built: every change this visitor made is gone."""
        v = visitor(request)
        visitors.reset(v)
        return PersonaOut(persona=v.persona)

    @app.post("/api/demo/shuffle")
    def shuffle(request: Request) -> PersonaOut:
        """Another Persona, under a new visitor id; the old file is deleted."""
        old = visitor(request)
        new = session.new_visitor(len(visitors.personas), avoid=old.persona)
        visitors.drop(old)
        visitors.open(new)
        visit: Visit = request.scope["fa_demo"]  # the object Guard holds
        visit.visitor, visit.fresh = new, True  # so Guard sends the new cookie
        return PersonaOut(persona=new.persona)

    index = cfg.dir / "web" / "index.html"

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{rest:path}", include_in_schema=False)
    def spa(rest: str = "") -> FileResponse:
        """As vercel.json: /app and /app/* are the one-page app."""
        return FileResponse(index)

    # create_app mounts the web build at "/" last; the routes above must precede
    # it, or a POST to /api/demo/* reaches the static files (405)
    app.router.routes.sort(key=lambda r: isinstance(r, Mount))
    app.add_middleware(Guard, secret=cfg.secret, visitors=visitors)
    return app


def __getattr__(name: str) -> FastAPI:
    """`app` is built on first access, so importing this module (tests, the
    import-linter) never needs the demo's environment."""
    if name == "app":
        return build()
    raise AttributeError(name)
