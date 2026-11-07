"""The Assistant graph (#139, ADR 0008, docs/AI_DESIGN.md): a Router sends each
message to one sub-graph (Ask, Memory, Correct, or the Plan stub). Each runs
its allowlisted tools, and every reply passes the one grounding check:
regenerate once, then the deterministic table. A Proposal pauses the graph
(`interrupt`) until the user confirms or rejects it; checkpoints live in the
encrypted database (`LedgerSaver`).

The checkpointed state holds the turn's message, reply and route, never tool
results. Nothing here logs message, reply or tool text."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Literal, NotRequired, TypedDict, cast

from langgraph.graph import (  # pyright: ignore[reportMissingTypeStubs]
    END,
    START,
    StateGraph,
)
from langgraph.types import Command, interrupt
from pydantic import ValidationError
from sqlalchemy import Connection

from finance_agent.assistant import context
from finance_agent.assistant.chat_model import ChatModel, Message, Reply
from finance_agent.assistant.checkpoints import LedgerSaver
from finance_agent.assistant.grounding import (
    ToolValue,
    check_grounding,
    render_fallback,
)
from finance_agent.assistant.ollama import OllamaError
from finance_agent.assistant.tools import NoData, ToolNotAllowed, Turn, run, specs
from finance_agent.assistant.tools.propose import ProposalOut
from finance_agent.assistant.values import tool_values
from finance_agent.identity import current_user
from finance_agent.ledger.answer_cache import AnswerCache
from finance_agent.ledger.conversation import Conversations
from finance_agent.ledger.proposals import Proposals

Route = Literal["ask", "plan", "memory", "correct"]
Path = Literal["cache", "local_model", "deterministic"]
ROUTES: tuple[Route, ...] = ("ask", "plan", "memory", "correct")
MAX_ROUNDS = 3  # model calls that may ask for tools in one turn
MAX_CALLS = 4  # tool calls run per round
CACHED = ("ask",)  # read-only answers; Proposals are never cached (ADR 0010)
HISTORY = 12  # messages kept in the thread for the conversation slot
PLAN_REPLY = (
    "Planning tools aren't available yet, so I can't plan or run a what-if."
    " I can answer questions about your spending."
)
MODEL_DOWN = "The local model isn't answering, so I can't help with that right now."
PROPOSED = "Here's the change I'd make. Nothing changes until you confirm it."
REGENERATE = (
    "Some figures in that reply are not in this turn's tool results. Rewrite it"
    " using only figures copied exactly from the tool results, or none."
)


def _keep_last(
    a: list[dict[str, str]], b: list[dict[str, str]]
) -> list[dict[str, str]]:
    return (a + b)[-HISTORY:]


class State(TypedDict):
    message: str
    turn_id: str
    today: str  # ISO date
    route: NotRequired[Route]
    reply: NotRequired[str]
    path: NotRequired[Path]
    evidence: NotRequired[list[str]]
    proposal_id: NotRequired[int | None]
    history: NotRequired[Annotated[list[dict[str, str]], _keep_last]]


type Update = dict[str, Any]  # the keys of State a node sets


@dataclass
class Draft:
    """One sub-graph's work this turn; never checkpointed."""

    reply: str = ""
    values: list[ToolValue] = field(default_factory=list[ToolValue])
    proposal_id: int | None = None
    messages: list[Message] = field(default_factory=list[Message])
    failed: bool = False
    rounds: list[list[dict[str, Any]]] = field(
        default_factory=list[list[dict[str, Any]]]
    )
    cached: bool = False


@dataclass
class Assistant:
    model: ChatModel
    conn: Connection
    today: date
    saver: LedgerSaver = field(init=False)

    # --- nodes ---------------------------------------------------------------

    def router(self, s: State) -> Update:
        try:
            r = self.model.chat(
                [
                    Message(role="system", content=context.SYSTEM["router"]),
                    Message(role="user", content=s["message"]),
                ]
            )
            word = r.content.strip().split()[0].strip(".,:!\"'").lower()
        except (OllamaError, OSError, IndexError):
            word = ""
        route = next((r for r in ROUTES if r == word), "ask")
        return {"route": route, "proposal_id": None, "evidence": []}

    def plan(self, s: State) -> Update:
        return {"reply": PLAN_REPLY, "path": "deterministic"}

    def subgraph(self, route: Route) -> Callable[[State], Update]:
        def node(s: State) -> Update:
            d = self._tools(route, s)
            if d.failed and not d.values:
                return {"reply": MODEL_DOWN, "path": "deterministic"}
            if not d.reply.strip() and d.proposal_id is None:
                d.failed = True
            return self._ground(route, s, d)

        return node

    def finish(self, s: State) -> Update:
        return {
            "history": [
                {"role": "user", "content": s["message"]},
                {"role": "assistant", "content": s.get("reply", "")},
            ]
        }

    def confirm(self, s: State) -> Update:
        pid = s.get("proposal_id")
        if pid is None:
            return {}
        decision = interrupt({"proposal_id": pid})
        props = Proposals(self.conn, current_user())
        try:
            if decision == "confirm":
                props.confirm(pid)
                reply = "Done: I've made that change."
            else:
                props.reject(pid)
                reply = "OK, I've left everything as it was."
        except ValueError:  # decided elsewhere, or its target changed
            reply = "That change can't be made now, so nothing was changed."
        return {
            "reply": reply,
            "path": "deterministic",
            "proposal_id": None,
            "history": [{"role": "assistant", "content": reply}],
        }

    # --- the work ------------------------------------------------------------

    def _cache(self) -> AnswerCache:
        return AnswerCache(self.conn, current_user())

    def _turn(self, s: State) -> Turn:
        return Turn(turn_id=s["turn_id"], quote=s["message"], today=self.today)

    def _tools(self, route: Route, s: State) -> Draft:
        memories = Proposals(self.conn, current_user()).memories()
        d = Draft(
            messages=context.assemble(
                route, s["message"], memories, s.get("history", [])
            )
        )
        budget = context.ToolBudget()
        offered = specs(route)
        for _ in range(MAX_ROUNDS):
            try:
                r = self.model.chat(d.messages, offered)
            except (OllamaError, OSError):
                d.failed = True
                return d
            if not r.tool_calls:
                d.reply = r.content
                return d
            calls = list(r.tool_calls[:MAX_CALLS])
            d.rounds.append([c.model_dump() for c in calls])
            if route in CACHED and len(d.rounds) == 1:
                hit = self._cache().get(route, d.rounds[0], self.today)
                if hit is not None:
                    d.reply, d.cached = hit, True
                    return d
            d.messages.append(
                Message(role="assistant", content=r.content, tool_calls=tuple(calls))
            )
            for call in calls:
                content = self._run(route, call, s, d)
                d.messages.append(
                    Message(
                        role="tool", tool_name=call.name, content=budget.take(content)
                    )
                )
        d.failed = True  # still asking for tools: no reply to check
        return d

    def _run(self, route: Route, call: Any, s: State, d: Draft) -> str:
        try:
            out = run(route, call, self.conn, self._turn(s))
        except ToolNotAllowed:
            return '{"error": "that tool is not available here"}'
        except NoData:
            return '{"error": "no data for that"}'
        except ValidationError:  # its message would quote the arguments
            return '{"error": "invalid arguments"}'
        except ValueError:  # UnknownTarget, InvalidSplit, ...: refused
            return '{"error": "refused: no such Transaction or change"}'
        if isinstance(out, ProposalOut):
            d.proposal_id = out.proposal_id
        d.values += tool_values(call.name, out)
        return out.model_dump_json()

    def _ground(self, route: Route, s: State, d: Draft) -> Update:
        if d.cached:
            return {"reply": d.reply, "path": "cache", "evidence": []}
        if not d.reply.strip() and d.proposal_id is not None:
            d.reply = PROPOSED
        res = check_grounding(d.reply, d.values, today=self.today)
        if not d.failed and not res.grounded:
            try:
                again: Reply = self.model.chat(
                    [
                        *d.messages,
                        Message(role="assistant", content=d.reply),
                        Message(role="user", content=REGENERATE),
                    ]
                )
                d.reply = again.content
                res = check_grounding(d.reply, d.values, today=self.today)
            except (OllamaError, OSError):
                d.failed = True
        if d.failed or not res.grounded:
            return {
                "reply": render_fallback(d.values),
                "path": "deterministic",
                "proposal_id": d.proposal_id,
                "evidence": sorted({v.evidence_id for v in d.values}),
            }
        # one round only: the key (its tool calls) then holds everything read
        if route in CACHED and len(d.rounds) == 1 and d.proposal_id is None:
            self._cache().put(route, d.rounds[0], self.today, d.reply)
        return {
            "reply": d.reply,
            "path": "local_model",
            "proposal_id": d.proposal_id,
            "evidence": list(res.evidence_ids),
        }

    # --- wiring --------------------------------------------------------------

    def compile(self) -> Any:
        g: Any = StateGraph(State)
        g.add_node("router", self.router)
        g.add_node("plan", self.plan)
        for r in ("ask", "memory", "correct"):
            g.add_node(r, self.subgraph(r))
        g.add_node("finish", self.finish)
        g.add_node("confirm", self.confirm)
        g.add_edge(START, "router")
        g.add_conditional_edges("router", _route, list(ROUTES))
        for r in ROUTES:
            g.add_edge(r, "finish")
        g.add_edge("finish", "confirm")
        g.add_edge("confirm", END)
        self.saver = LedgerSaver(Conversations(self.conn, current_user()))
        return g.compile(checkpointer=self.saver)

    def invoke(self, payload: Any, thread_id: str) -> dict[str, Any]:
        """Run the graph on the thread, then write its checkpoints here."""
        cfg = {"configurable": {"thread_id": thread_id}}
        out = self.compile().invoke(payload, cfg, durability="sync")
        self.saver.flush()
        return out


def _route(state: State) -> str:
    return state.get("route", "ask")


@dataclass(frozen=True)
class Answer:
    thread_id: str
    turn_id: str
    route: str
    reply: str
    path: Path
    evidence: tuple[str, ...]
    proposal_id: int | None  # a confirm card is waiting for this Proposal


def _answer(thread: str, out: dict[str, Any]) -> Answer:
    pending = out.get("__interrupt__") or ()
    return Answer(
        thread_id=thread,
        turn_id=out.get("turn_id", ""),
        route=out.get("route", "ask"),
        reply=out.get("reply", ""),
        path=out.get("path", "deterministic"),
        evidence=tuple(out.get("evidence") or ()),
        proposal_id=out.get("proposal_id") if pending else None,
    )


def ask(assistant: Assistant, message: str, thread_id: str | None = None) -> Answer:
    """Run one turn on a thread (a new one when `thread_id` is None)."""
    thread = thread_id or uuid.uuid4().hex
    out = assistant.invoke(
        {
            "message": message,
            "turn_id": uuid.uuid4().hex,
            "today": assistant.today.isoformat(),
        },
        thread,
    )
    return _answer(thread, out)


def waiting_for(assistant: Assistant, thread_id: str) -> int | None:
    """The Proposal the thread is paused on, if any."""
    snap = assistant.compile().get_state({"configurable": {"thread_id": thread_id}})
    if not snap.interrupts:
        return None
    return cast(dict[str, int], snap.interrupts[0].value)["proposal_id"]


def resume(
    assistant: Assistant, thread_id: str, decision: Literal["confirm", "reject"]
) -> Answer:
    return _answer(thread_id, assistant.invoke(Command(resume=decision), thread_id))
