"""The Assistant graph (#139, ADR 0008, docs/AI_DESIGN.md): a Router sends each
message to one sub-graph (Ask, Memory, Correct or Plan). Each runs
its allowlisted tools, and every reply passes the one grounding check:
regenerate once, then the deterministic table. A Proposal pauses the graph
(`interrupt`) until the user confirms or rejects it; checkpoints live in the
encrypted database (`LedgerSaver`).

The checkpointed state holds the turn's message, reply and route, never tool
results. Nothing here logs message, reply or tool text."""

import re
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

from finance_agent.assistant import context, fast_path
from finance_agent.assistant.chat_model import (
    ChatModel,
    Message,
    Reply,
    ToolCall,
    printed_tool_call,
)
from finance_agent.assistant.checkpoints import LedgerSaver
from finance_agent.assistant.grounding import (
    ToolValue,
    check_grounding,
    render_fallback,
)
from finance_agent.assistant.ollama import OllamaError
from finance_agent.assistant.tools import (
    TOOLS,
    NoData,
    ToolNotAllowed,
    Turn,
    run,
    specs,
)
from finance_agent.assistant.tools.propose import FigureInMemory, NotSaid, ProposalOut
from finance_agent.assistant.tools.query import QueryPlan, SpendForQueryOut
from finance_agent.assistant.values import tool_values
from finance_agent.identity import current_user
from finance_agent.ledger.answer_cache import AnswerCache, Hit
from finance_agent.ledger.conversation import Conversations
from finance_agent.ledger.proposals import Proposals

Route = Literal["ask", "plan", "memory", "correct"]
Path = Literal["cache", "local_model", "deterministic", "planner"]
ROUTES: tuple[Route, ...] = ("ask", "plan", "memory", "correct")
MAX_ROUNDS = 3  # model calls that may ask for tools in one turn
MAX_CALLS = 4  # tool calls run per round
CACHED = ("ask",)  # read-only answers; Proposals are never cached (ADR 0010)
# Insight ratings and dismissals change these without a Ledger version bump; a
# spend_for_query answer depends on the thread's plan and must set it (#271)
UNCACHED = frozenset({"list_insights", "explain_insight", "spend_for_query"})
HISTORY = 12  # messages kept in the thread for the conversation slot
PREFETCH_TOKENS = 1500  # retrieve-then-generate context cap, read in one prefill
SEARCH_HITS = 5
# the cache key of an answer to a thread's first message, looked up before any
# model call: the question, Ledger version, day and Memories fix every input
FIRST = ({"first_message": True},)
MODEL_DOWN = "The local model isn't answering, so I can't help with that right now."
PROPOSED = "Here's the change I'd make. Nothing changes until you confirm it."
NO_FIGURE_MEMORIES = (
    "I don't remember money figures or spending patterns, only preferences,"
    " goals and constraints in your words."
)
REGENERATE = (
    "Some figures in that reply are not in this turn's tool results. Rewrite it"
    " using only figures copied exactly from the tool results, or none, and"
    " with no links."
)
# A link, image or address in a reply could carry the user's data off the device
# when rendered or followed (#142), so such a reply is never shown or cached.
_LINK = re.compile(
    r"\b[a-z][a-z0-9+.-]*://|//|\bwww\.|\]\(|</?[a-z]+[\s>/]"
    r"|\b(?:mailto|javascript|file)\s*:|\bdata:\s*[a-z]+/"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}[/?]",
    re.IGNORECASE,
)


_UNSURE = re.compile(
    r"\b(will|next|afford|forecast|going to|plan|save|saving|savings|move|send"
    r"|transfer|pay (?:off|for)|can i|could i|should i|would|split"
    r"|recategori[sz]e|categori[sz]e|actually|really|note|ignore|forget)\b",
    re.IGNORECASE,
)
_MEMORY = re.compile(r"^\s*(please\s+)?remember\b|\bwhat do you remember\b", re.I)
_ASK = re.compile(
    r"\bhow much\b|\bwhat did\b|\bwhat came in\b|\bwhere did i\b"
    r"|\bsubscriptions?\b|\bmy net\b",
    re.IGNORECASE,
)


def pre_route(message: str) -> Route | None:
    """The route of an obvious message without a Router call (saves one model
    call a turn); None sends it to the model Router. Anything about the
    future, a write or a payment is never obvious."""
    if _UNSURE.search(message):
        return None
    if _MEMORY.search(message):
        return "memory"
    return "ask" if _ASK.search(message) else None


def _shown(reply: str) -> bool:
    """A reply may reach the user only with no link and no tool call printed as
    text (a failed generation, #143), whatever its figures."""
    return not _LINK.search(reply) and not printed_tool_call(reply, frozenset(TOOLS))


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
    # the last spend_for_query plan (#271): selectors and ids, never figures
    query_plan: NotRequired[dict[str, Any] | None]


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
    cached: tuple[str, ...] | None = None  # a cache hit's evidence IDs
    refusal: str | None = None  # a fixed reply that replaces the model's
    plan: QueryPlan | None = None  # this turn's last spend_for_query plan
    prefetched: int = 0  # values[:prefetched] came from _prefetch


@dataclass
class Assistant:
    model: ChatModel
    conn: Connection
    today: date
    progress: Callable[[str], None] | None = None  # a tool name, or "model"
    prefetch: bool = False  # retrieve-then-generate (the app turns it on)
    saver: LedgerSaver = field(init=False)

    # --- nodes ---------------------------------------------------------------

    def router(self, s: State) -> Update:
        route = pre_route(s["message"])
        if route is None and (
            fast_path.parse(s["message"], self.today) or self._first_hit("ask", s)
        ):
            route = "ask"
        if route is not None:
            return {"route": route, "proposal_id": None, "evidence": []}
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

    def subgraph(self, route: Route) -> Callable[[State], Update]:
        def node(s: State) -> Update:
            if route == "ask" and (fast := self._fast(s)) is not None:
                return fast
            d = self._tools(route, s)
            if d.failed and not d.values:
                return {"reply": MODEL_DOWN, "path": "deterministic"}
            if not d.reply.strip() and d.proposal_id is None:
                d.failed = True
            out = self._ground(route, s, d)
            if d.plan is not None:
                out["query_plan"] = d.plan.model_dump(mode="json")
            return out

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
                route, s["message"], memories, s.get("history", []), self.today
            )
        )
        if (hit := self._first_hit(route, s)) is not None:
            d.reply, d.cached = hit
            return d
        if route == "ask" and self.prefetch:
            self._prefetch(s, d)
        budget = context.ToolBudget()
        offered = specs(route)
        for _ in range(MAX_ROUNDS):
            self._say("model")
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
            if self._cacheable(route, d) and len(d.rounds) == 1:
                hit = self._cache().get(route, s["message"], d.rounds[0], self.today)
                if hit is not None:
                    d.reply, d.cached = hit
                    return d
            d.messages.append(
                Message(role="assistant", content=r.content, tool_calls=tuple(calls))
            )
            for call in calls:
                seen = len(d.values)
                content = budget.take(self._run(route, call, s, d))
                if content == context.TOO_LARGE:  # unseen, so not evidence
                    del d.values[seen:]
                d.messages.append(
                    Message(role="tool", tool_name=call.name, content=content)
                )
        d.failed = True  # still asking for tools: no reply to check
        return d

    def _first_hit(self, route: Route, s: State) -> Hit | None:
        """A cached answer to this message as a thread's first, if any."""
        if route not in CACHED or s.get("history"):
            return None
        return self._cache().get(route, s["message"], FIRST, self.today)

    def _say(self, step: str) -> None:
        if self.progress is not None:
            self.progress(step)

    def _fast(self, s: State) -> Update | None:
        """A simple spending question with no model call: the planner's one
        tool call and a template (fast_path), checked like any reply. Cached
        by that call, as a model's answer to it would be."""
        call = fast_path.parse(s["message"], self.today)
        if call is None:
            return None
        calls = [call.model_dump()]
        hit = self._cache().get("ask", s["message"], calls, self.today)
        if hit is not None:
            return {"reply": hit.reply, "path": "cache", "evidence": list(hit.evidence)}
        d = Draft()
        out = self._call("ask", call, s, d)
        reply = fast_path.render(out) if out is not None else None
        if (
            reply is None
            or not check_grounding(reply, d.values, today=self.today).grounded
        ):
            return None  # no data, or a template slip: the model answers
        evidence = sorted({v.evidence_id for v in d.values})
        self._cache().put("ask", s["message"], calls, self.today, reply, evidence)
        return {"reply": reply, "path": "planner", "evidence": evidence}

    def _prefetch(self, s: State, d: Draft) -> None:
        """Retrieve-then-generate: the tool results an answer most likely
        needs, fetched with no model call and shown as compact `key: value`
        facts just before the question, so the prompt before them (system,
        Memories, last turns) is unchanged and Ollama reuses its KV cache.
        One model call can word the answer; it may still call a tool."""
        wanted = [
            ToolCall(name="spending_summary", arguments={}),
            ToolCall(name="spending_summary", arguments={"month": _last(self.today)}),
            ToolCall(name="get_story", arguments={}),
        ]
        if (guess := fast_path.guess(s["message"], self.today)) is not None:
            wanted.append(guess)
        query = " ".join(s["message"].split())[:100]
        wanted.append(
            ToolCall(name="search", arguments={"query": query, "limit": SEARCH_HITS})
        )
        left = PREFETCH_TOKENS * context.CHARS_PER_TOKEN
        made: list[str] = []
        for call in wanted:
            seen = len(d.values)
            out = self._call("ask", call, s, d)
            content = context.facts(call.name, out) if out is not None else ""
            if out is None or len(content) > left:
                del d.values[seen:]  # unseen, so not evidence
                continue
            left -= len(content)
            made.append(content)
        if made:
            body = "\n".join(made)
            d.messages.insert(-1, Message(role="system", content=context.FACTS + body))
        d.prefetched = len(d.values)

    def _call(self, route: Route, call: ToolCall, s: State, d: Draft) -> Any:
        """Run a call the graph made itself; None when it gives nothing."""
        self._say(call.name)
        try:
            out = run(route, call, self.conn, self._turn(s), _plan(s))
        except (ToolNotAllowed, NoData, ValidationError, ValueError):
            return None
        d.values += tool_values(call.name, out)
        return out

    @staticmethod
    def _cacheable(route: Route, d: Draft) -> bool:
        return route in CACHED and not any(
            c["name"] in UNCACHED for r in d.rounds for c in r
        )

    def _run(self, route: Route, call: Any, s: State, d: Draft) -> str:
        self._say(call.name)
        try:
            out = run(route, call, self.conn, self._turn(s), d.plan or _plan(s))
        except ToolNotAllowed:
            return '{"error": "that tool is not available here"}'
        except NoData:
            return '{"error": "no data for that"}'
        except ValidationError:  # its message would quote the arguments
            return '{"error": "invalid arguments"}'
        except FigureInMemory:
            d.refusal = NO_FIGURE_MEMORIES
            return '{"error": "refused: a Memory never holds a money figure"}'
        except NotSaid:
            return (
                '{"error": "refused: that amount or date is not in what the user'
                ' said this turn; ask them for it, do not guess"}'
            )
        except ValueError:  # UnknownTarget, InvalidSplit, ...: refused
            return '{"error": "refused: no such Transaction or change"}'
        if isinstance(out, ProposalOut):
            d.proposal_id = out.proposal_id
        if isinstance(out, SpendForQueryOut):
            d.plan = out.plan
        d.values += tool_values(call.name, out)
        return out.model_dump_json()

    def _ground(self, route: Route, s: State, d: Draft) -> Update:
        if d.cached is not None:
            return {"reply": d.reply, "path": "cache", "evidence": list(d.cached)}
        if d.refusal is not None and d.proposal_id is None:
            return {"reply": d.refusal, "path": "deterministic"}
        if not d.reply.strip() and d.proposal_id is not None:
            d.reply = PROPOSED
        res = check_grounding(d.reply, d.values, today=self.today)
        ok = res.grounded and _shown(d.reply)
        if not d.failed and not ok:
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
                ok = res.grounded and _shown(d.reply)
            except (OllamaError, OSError):
                d.failed = True
        if d.failed or not ok:
            shown = d.values[d.prefetched :] or d.values  # what the model asked for
            return {
                "reply": render_fallback(shown),
                "path": "deterministic",
                "proposal_id": d.proposal_id,
                "evidence": sorted({v.evidence_id for v in shown}),
            }
        # one round only: the key (its tool calls) then holds everything read
        if self._cacheable(route, d) and len(d.rounds) == 1 and d.proposal_id is None:
            self._cache().put(
                route, s["message"], d.rounds[0], self.today, d.reply, res.evidence_ids
            )
        first = not s.get("history") and len(d.rounds) <= 1  # never multi-round
        if first and self._cacheable(route, d) and d.proposal_id is None:
            self._cache().put(
                route, s["message"], FIRST, self.today, d.reply, res.evidence_ids
            )
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
        for r in ROUTES:
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


def _last(today: date) -> str:
    n = today.year * 12 + today.month - 2
    return f"{n // 12}-{n % 12 + 1:02d}"


def _plan(state: State) -> QueryPlan | None:
    """The thread's last QueryPlan; None when there is none or it no longer
    validates (an older checkpoint)."""
    raw = state.get("query_plan")
    try:
        return QueryPlan.model_validate(raw) if raw else None
    except ValidationError:
        return None


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
