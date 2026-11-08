"""The Assistant graph (#139, ADR 0008, docs/AI_DESIGN.md): a Router sends each
message to one sub-graph (Ask, Memory, Correct or Plan). Each runs
its allowlisted tools, and every reply passes the one grounding check:
regenerate once, then the deterministic table. A Proposal pauses the graph
(`interrupt`) until the user confirms or rejects it; checkpoints live in the
encrypted database (`LedgerSaver`).

The checkpointed state holds the turn's message, reply and route, never tool
results. Nothing here logs message, reply or tool text."""

import logging
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Generator, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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

from finance_agent.assistant import context, fast_path, planner, resolve
from finance_agent.assistant.chat_model import (
    ChatModel,
    Message,
    Reply,
    ToolCall,
    printed_tool_call,
)
from finance_agent.assistant.checkpoints import LedgerSaver
from finance_agent.assistant.cloud_route import CloudRoute, Name, named_in, names
from finance_agent.assistant.evidence import Evidence, counted_all
from finance_agent.assistant.grounding import (
    ToolValue,
    check_grounding,
    format_figure,
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
from finance_agent.assistant.tools import map as map_tool
from finance_agent.assistant.tools.propose import FigureInMemory, NotSaid, ProposalOut
from finance_agent.assistant.tools.query import QueryPlan, SpendForQueryOut
from finance_agent.assistant.tools.read import (
    Source,
    _category,  # pyright: ignore[reportPrivateUsage]
)
from finance_agent.assistant.values import tool_values
from finance_agent.engine import profile
from finance_agent.identity import current_user
from finance_agent.ledger import Ledger
from finance_agent.ledger.answer_cache import AnswerCache, Hit
from finance_agent.ledger.conversation import Conversations
from finance_agent.ledger.map_edges import MapEdges
from finance_agent.ledger.proposals import Proposals
from finance_agent.ledger.taxonomy import TAXONOMY

Route = Literal["ask", "plan", "memory", "correct"]
Path = Literal["cache", "local_model", "cloud", "deterministic", "planner", "miss"]
ROUTES: tuple[Route, ...] = ("ask", "plan", "memory", "correct")
MAX_ROUNDS = 3  # model calls that may ask for tools in one turn
MAX_CALLS = 4  # tool calls run per round
CLOUD = ("ask", "plan")  # Memory and Correct stay local: no Proposal from the cloud
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
log = logging.getLogger(__name__)
# questions no plan or checked reply answered, by reason code: never the text
# (the bench reads it to find coverage gaps)
MISSES: Counter[str] = Counter()
MISS = "I can't answer that one yet. Here are some things I can tell you:"
MISS_CHIPS = (
    "How much did I spend last month?",
    "What's my balance?",
    "Where can I save?",
)
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


ROUTE_STAGE: dict[str, str] = {
    "ask": "Looking through your ledger…",
    "plan": "Running the numbers…",
    "memory": "Noting that down…",
    "correct": "Finding that transaction…",
}
TOOL_STAGE = {
    "spending_summary": "Adding up your month…",
    "compare_months": "Comparing your months…",
    "find_transactions": "Searching your transactions…",
    "search": "Searching your transactions…",
    "list_subscriptions": "Checking your subscriptions…",
    "subscription_advice": "Checking your subscriptions…",
    "forecast_cashflow": "Forecasting the coming months…",
    "simulate_purchase": "Checking that purchase against your plan…",
}
CHECKING = "Checking every figure…"
WRITING = "Writing your answer…"
TOPICS = 12  # turns whose Plan topics feed the rolling summary


def tool_stage(name: str, args: dict[str, Any]) -> str:
    """Progress words for a tool call. A Category is named only when it is a
    taxonomy name, so no model or bank text is echoed back."""
    if name in ("category_detail", "spend_for_query", "category_trend"):
        raw = args.get("category")
        cats = args.get("categories")
        if raw is None and isinstance(cats, list) and cats:
            raw = cast(list[Any], cats)[0]
        if isinstance(raw, str):
            top, sub = _category(raw)
            subs = TAXONOMY.get(top, ())
            leaf = next(
                (x for x in subs if sub and x.casefold() == sub.casefold()), None
            )
            if top in TAXONOMY and (leaf or not sub):
                return f"Finding your {(leaf or top).lower()} spending…"
        return "Finding that spending…"
    return TOOL_STAGE.get(name, "Checking your ledger…")


def _shown(reply: str) -> bool:
    """A reply may reach the user only with no link and no tool call printed as
    text (a failed generation, #143), whatever its figures."""
    return not _LINK.search(reply) and not printed_tool_call(reply, frozenset(TOOLS))


def _keep_last(
    a: list[dict[str, str]], b: list[dict[str, str]]
) -> list[dict[str, str]]:
    return (a + b)[-HISTORY:]


def _keep_topics(a: list[str], b: list[str]) -> list[str]:
    return (a + b)[-TOPICS:]


class State(TypedDict):
    message: str
    turn_id: str
    today: str  # ISO date
    thread_id: NotRequired[str]  # the payload log's conversation id
    route: NotRequired[Route]
    reply: NotRequired[str]
    path: NotRequired[Path]
    evidence: NotRequired[list[str]]
    proposal_id: NotRequired[int | None]
    history: NotRequired[Annotated[list[dict[str, str]], _keep_last]]
    cloud: NotRequired[bool]  # this thread's opt-in (#301); kept until changed
    cost_pence: NotRequired[int | None]  # this turn's cloud call, if any
    payload_id: NotRequired[str | None]
    # the last spend_for_query plan (#271): selectors and ids, never figures
    query_plan: NotRequired[dict[str, Any] | None]
    # this turn's planner Plan (#317): selectors and labels, never figures;
    # the next turn's follow-up patches it
    turn_plan: NotRequired[dict[str, Any] | None]
    resolved: NotRequired[list[str]]  # what code resolved, shown with the reply
    chips: NotRequired[list[str]]  # follow-ups the planner answers with 0 calls
    topics: NotRequired[Annotated[list[str], _keep_topics]]  # one per turn


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
    names: list[Name] = field(default_factory=list[Name])  # for the redactor
    plan: QueryPlan | None = None  # this turn's last spend_for_query plan
    prefetched: int = 0  # values[:prefetched] came from _prefetch
    relevant: list[ToolValue] | None = None  # the Plan's own facts, if planned
    advice: tuple[str, list[str]] | None = None  # an open answer's code fallback


@dataclass
class Assistant:
    model: ChatModel
    conn: Connection
    today: date
    prefetch: bool = False  # retrieve-then-generate (the app turns it on)
    saver: LedgerSaver = field(init=False)
    cloud: CloudRoute | None = None  # None: no key, or the demo (#296)
    on_stage: Callable[[str], None] | None = None  # progress words, no data
    # Transactions behind each spending total this turn, by evidence ID;
    # shown to the user only, never checkpointed (ADR 0007)
    counted: dict[str, Evidence] = field(default_factory=dict[str, Evidence])
    # this turn's Plan or clarifying question, found by the router (#317)
    _now: planner.Plan | planner.Clarify | None = field(default=None, init=False)
    timing: dict[str, float] = field(default_factory=dict[str, float], init=False)
    use_map: bool = True  # the personal map block (#280); off for the ablation
    model_calls: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.model = _Timed(self.model, self)

    @contextmanager
    def _phase(self, name: str) -> Generator[None]:
        """Monotonic ms per phase (plan, fetch, model, check), for the reply's
        timing line. Nested phases are not double counted: model time inside a
        phase is taken out of it."""
        start, model_before = time.monotonic(), self.timing.get("model", 0.0)
        try:
            yield
        finally:
            took = (time.monotonic() - start) * 1000
            inner = self.timing.get("model", 0.0) - model_before
            self.timing[name] = self.timing.get(name, 0.0) + took - inner

    def _check(self, reply: str, values: Any, *, today: date) -> Any:
        with self._phase("check"):
            return check_grounding(reply, values, today=today)

    def _evidence(self, led: Ledger, out: Any) -> None:
        for ev in counted_all(led, out):
            self.counted[ev.evidence_id] = ev

    def _stage(self, words: str) -> None:
        if self.on_stage is not None:
            self.on_stage(words)

    # --- nodes ---------------------------------------------------------------

    def router(self, s: State) -> Update:
        with self._phase("plan"):
            self._now = None if self._cloud_on(s) else self._plan_turn(s)
        if self._now is not None:
            now = self._now
            deep = isinstance(now, planner.Plan) and now.kind == "in_depth"
            now_route: Route = "plan" if deep else "ask"
            self._stage(ROUTE_STAGE[now_route])
            return {"route": now_route, **_NEW_TURN}
        route = pre_route(s["message"])
        fast = not self._cloud_on(s) and fast_path.parse(s["message"], self.today)
        if route is None and (fast or self._first_hit("ask", s)):
            route = "ask"
        if route is not None:
            self._stage(ROUTE_STAGE[route])
            return {"route": route, **_NEW_TURN}
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
        self._stage(ROUTE_STAGE[route])
        return {"route": route, **_NEW_TURN}

    def subgraph(self, route: Route) -> Callable[[State], Update]:
        def node(s: State) -> Update:
            # a thread opted in to cloud wording keeps it (#301)
            if self._now is not None and route in ("ask", "plan"):
                if (done := self._planned(route, s, self._now)) is not None:
                    return done
            fast = None if self._cloud_on(s) else self._fast(s)
            if route == "ask" and fast is not None:
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
        plan = s.get("turn_plan") or {}
        return {
            "history": [
                {"role": "user", "content": s["message"]},
                {"role": "assistant", "content": s.get("reply", "")},
            ],
            "topics": [str(plan.get("what") or plan.get("kind") or "")],
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
            **_NEW_TURN,
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
        hit = self._first_hit(route, s)
        # an opted-in cloud turn sends its tool values, so it is not widened
        cloud = self._cloud_on(s)
        if route == "ask" and self.prefetch and not cloud:
            self._prefetch(s, d)  # on a hit, re-read only for the Transactions shown
        if hit is not None:
            d.reply, d.cached = hit
            return d
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
            if self._cacheable(route, d) and len(d.rounds) == 1:
                hit = self._cache().get(route, s["message"], d.rounds[0], self.today)
                if hit is not None:
                    d.reply, d.cached = hit
                    for call in calls:  # re-read only for the Transactions shown
                        self._run(route, call, s, d)
                    return d
            d.messages.append(
                Message(role="assistant", content=r.content, tool_calls=tuple(calls))
            )
            for call in calls:
                self._stage(tool_stage(call.name, call.arguments))
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

    def _cloud_on(self, s: State) -> bool:
        return bool(s.get("cloud")) and self.cloud is not None

    # --- the planner (#317) ------------------------------------------------

    def _plan_turn(self, s: State) -> planner.Plan | planner.Clarify | None:
        """This turn's Plan with no model call: a follow-up patching the last
        one, an open or in-depth question, or a figure question the fast path
        does not take. None: the fast path or the model."""
        msg, led = s["message"], Ledger(self.conn, current_user())
        mems = Proposals(self.conn, current_user()).memories()
        prev = _turn_plan(s)
        if prev is not None:
            fu = planner.follow_up(prev, msg, self.today, led, mems)
            if fu is not None:
                return fu
        if (bal := planner.balances_plan(msg, self.today)) is not None:
            return bal
        if (kind := planner.classify(msg)) is not None:
            return planner.open_plan(kind, msg, self.today, led, mems)
        plan = planner.figure(msg, self.today, led, mems)
        if fast_path.parse(msg, self.today) is not None:
            # the fast path, unless the planner counts Merchants filed elsewhere
            return plan if isinstance(plan, planner.Plan) and plan.note else None
        return plan

    def _planned(
        self, route: Route, s: State, now: planner.Plan | planner.Clarify
    ) -> Update | None:
        if isinstance(now, planner.Clarify):
            return {"reply": now.question, "path": "planner", "evidence": []}
        if now.kind in ("open", "in_depth"):
            return self._write(route, s, now)
        if now.kind == "balances":
            return self._balances(s, now)
        return self._figure(s, now)

    def _balances(self, s: State, plan: planner.Plan) -> Update | None:
        """Account balances and overdrafts from a template: no model call."""
        with self._phase("fetch"):
            out = planner.balances(Source(self.conn, current_user(), self.today))
        values = tool_values("balances", out)
        reply = planner.render_balances(plan, out)
        if (
            not _shown(reply)
            or not self._check(reply, values, today=self.today).grounded
        ):
            return None
        shown = [r for r in out.rows if r.balance is not None]
        resolved = [
            r.account.data
            + (f", balance as of {format_figure(r.as_of)}" if r.as_of else "")
            for r in shown
        ]
        return {
            "reply": reply,
            "path": "planner",
            "evidence": _shown_evidence(v.evidence_id for v in values),
            "turn_plan": plan.model_dump(mode="json"),
            "resolved": resolved,
        }

    def _figure(self, s: State, plan: planner.Plan) -> Update | None:
        """A figure Plan from a template: no model call."""
        led = Ledger(self.conn, current_user())
        self._stage(ROUTE_STAGE["ask"])
        try:
            with self._phase("fetch"):
                got = planner.execute(
                    plan, Source(self.conn, current_user(), self.today)
                )
        except (NoData, ValueError):
            return None
        values = planner.values(plan, led)
        for f in got:
            values += tool_values(f.name, f.out)
            self._evidence(led, f.out)
        reply = planner.render(plan, got)
        if reply is None or not _shown(reply):
            return None
        res = self._check(reply, values, today=self.today)
        if not res.grounded:
            return None  # a template slip: the model answers
        return {
            "reply": reply,
            "path": "planner",
            "evidence": _shown_evidence(v.evidence_id for v in values),
            "turn_plan": plan.model_dump(mode="json"),
            "resolved": list(plan.labels),
        }

    def _write(self, route: Route, s: State, plan: planner.Plan) -> Update:
        """An open or in-depth question: one fetch round of at most
        planner.MAX_FETCHES Engine reads, then one writer call with no tool
        schemas. Grounding as ever: one regeneration, then the table."""
        led = Ledger(self.conn, current_user())
        memories = Proposals(self.conn, current_user()).memories()
        profile, profile_values = profile_context(self.conn, current_user(), self.today)
        d = Draft(
            messages=context.writer(
                s["message"],
                memories,
                s.get("history", []),
                self.today,
                s.get("topics", []),
                profile,
            )
        )
        d.values += planner.values(plan, led) + profile_values
        left = PREFETCH_TOKENS * context.CHARS_PER_TOKEN
        made = ["[resolved]\n" + "\n".join(plan.labels)] if plan.labels else []
        calls = planner.fetches(plan)
        d.rounds.append([c.model_dump() for _, c in calls])  # caching rules see them
        main = len(d.values)
        outs = self._fetch_round(calls, s)
        took: list[Any] = []
        for i, ((r, call), got) in enumerate(zip(calls, outs, strict=True)):
            seen = len(d.values)
            out = self._took(call, got, d) if got is not None else None
            if got is _AGAIN:  # the parallel read failed: on the turn's own
                out = self._call(r, call, s, d)
            took.append(out)
            if i == 0:
                d.relevant = d.values[:main] + d.values[seen:]
            content = context.facts(call.name, out) if out is not None else ""
            if out is None or len(content) > left:
                del d.values[seen:]  # unseen, so not evidence
                continue
            left -= len(content)
            made.append(content)
        if self.use_map and (plan.merchant_ids or plan.categories):
            with self._phase("fetch"):
                src = Source(self.conn, current_user(), self.today)
                store = MapEdges(self.conn, current_user())
                nodes = map_tool.nodes_for(store, plan.merchant_ids, plan.categories)
                near = map_tool.block(map_tool.map_context(src, nodes))
            if near and len(near) <= left:
                made.append(near)
        d.advice = planner.advice(took)
        if d.advice is None and plan.kind == "open":  # never a figures table
            d.advice = (NO_ADVICE, [])
        made.append(context.LENGTH[plan.kind])
        body = "\n".join(made)
        d.messages.insert(-1, Message(role="system", content=context.FACTS + body))
        self._stage(WRITING)
        try:
            d.reply = self.model.chat(d.messages).content
        except (OllamaError, OSError):
            d.failed = True
        if d.failed and not d.values:
            return {"reply": MODEL_DOWN, "path": "deterministic"}
        if not d.reply.strip():
            d.failed = True
        out = self._ground_local(route, s, d)
        out["evidence"] = _shown_evidence(out.get("evidence") or ())
        return out | {
            "turn_plan": plan.model_dump(mode="json"),
            "resolved": list(plan.labels),
        }

    def _fast(self, s: State) -> Update | None:
        """A simple spending question with no model call: the planner's one
        tool call and a template (fast_path), checked like any reply. Not
        cached: the Engine read takes milliseconds and lists the Transactions
        shown."""
        call = fast_path.parse(s["message"], self.today)
        if call is None:
            return None
        d = Draft()
        out = self._call("ask", call, s, d)
        reply = fast_path.render(out) if out is not None else None
        if reply is None or not self._check(reply, d.values, today=self.today).grounded:
            return None  # no data, or a template slip: the model answers
        evidence = sorted({v.evidence_id for v in d.values})
        done: Update = {"reply": reply, "path": "planner", "evidence": evidence}
        led = Ledger(self.conn, current_user())
        plan = planner.figure(s["message"], self.today, led)
        if isinstance(plan, planner.Plan):  # so a follow-up can patch it
            done |= {"turn_plan": plan.model_dump(mode="json"), "resolved": []}
        return done

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
        self._stage(tool_stage(call.name, call.arguments))
        try:
            with self._phase("fetch"):
                out = run(route, call, self.conn, self._turn(s), _plan(s))
        except (ToolNotAllowed, NoData, ValidationError, ValueError):
            return None
        return self._took(call, out, d)

    def _took(self, call: ToolCall, out: Any, d: Draft) -> Any:
        if out is _AGAIN:
            return out
        self._evidence(Ledger(self.conn, current_user()), out)
        d.values += tool_values(call.name, out)
        return out

    def _fetch_round(
        self, calls: Sequence[tuple[Route, ToolCall]], s: State
    ) -> list[Any]:
        """The round's read-only Engine calls at once, each on its own pooled
        connection (SQLite serves readers side by side). A call that gives
        nothing is None; one that fails on its connection is _AGAIN, run
        again on the turn's own."""
        engine, turn, plan = self.conn.engine, self._turn(s), _plan(s)

        def one(rc: tuple[Route, ToolCall]) -> Any:
            try:
                with engine.connect() as c:
                    return run(rc[0], rc[1], c, turn, plan)
            except (ToolNotAllowed, NoData, ValidationError, ValueError):
                return None
            except Exception:  # noqa: BLE001  # a locked or private database
                return _AGAIN

        if len(calls) < 2 or engine.url.database in (None, "", ":memory:"):
            return [_AGAIN] * len(calls)  # nothing to overlap, or one shared database
        self._stage(tool_stage(calls[0][1].name, calls[0][1].arguments))
        with self._phase("fetch"), ThreadPoolExecutor(len(calls)) as pool:
            return list(pool.map(one, calls))

    @staticmethod
    def _cacheable(route: Route, d: Draft) -> bool:
        return route in CACHED and not any(
            c["name"] in UNCACHED for r in d.rounds for c in r
        )

    def _run(self, route: Route, call: Any, s: State, d: Draft) -> str:
        call = _pinned(call, s["message"], self.today)
        try:
            with self._phase("fetch"):
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
        self._evidence(Ledger(self.conn, current_user()), out)
        d.values += tool_values(call.name, out)
        if self.cloud is not None:
            d.names += names(out)
        return out.model_dump_json()

    def _ground(self, route: Route, s: State, d: Draft) -> Update:
        if d.cached is not None:
            return {"reply": d.reply, "path": "cache", "evidence": list(d.cached)}
        if d.refusal is not None and d.proposal_id is None:
            return {"reply": d.refusal, "path": "deterministic"}
        if not (
            s.get("cloud")
            and self.cloud is not None
            and route in CLOUD
            and d.proposal_id is None
        ):
            return self._ground_local(route, s, d)
        # the gateway sends only this turn's redacted tool facts: the profile
        # (prompt prose of the local writer) never reaches a cloud call.
        # shortcut: no profile figures for cloud wording; sending them as
        # facts needs the golden cloud recordings re-made with a real key
        w = self.cloud.word(
            s["message"],
            d.values,
            d.names + self._named(s["message"]),
            user_id=current_user(),
            thread_id=s.get("thread_id", ""),
        )
        meter = {"cost_pence": w.cost_pence, "payload_id": w.payload_id}
        if w.text is not None:
            res = self._check(w.text, d.values, today=self.today)
            if res.grounded and _shown(w.text):
                return {
                    "reply": w.text,
                    "path": "cloud",
                    "proposal_id": None,
                    "evidence": list(res.evidence_ids),
                    **meter,
                }
        return self._ground_local(route, s, d) | meter  # then local, then the table

    def _named(self, question: str) -> list[Name]:
        """Names the question mentions, whether or not a tool returned them."""
        ms = Ledger(self.conn, current_user()).merchants()
        cats = (c for m in ms for c in m.default_category or ())
        return named_in(question, (m.name for m in ms), cats)

    def _ground_local(self, route: Route, s: State, d: Draft) -> Update:
        if not d.reply.strip() and d.proposal_id is not None:
            d.reply = PROPOSED
        self._stage(CHECKING)
        res = self._check(d.reply, d.values, today=self.today)
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
                res = self._check(d.reply, d.values, today=self.today)
                ok = res.grounded and _shown(d.reply)
            except (OllamaError, OSError):
                d.failed = True
        if (d.failed or not ok) and d.advice is not None:
            reply, ids = d.advice
            return {"reply": reply, "path": "planner", "evidence": ids}
        if d.failed or not ok:
            # only facts that answer this question: the Plan's own read, or
            # what the model asked for; never the whole prefetched context
            shown = d.relevant if d.relevant is not None else d.values[d.prefetched :]
            if not shown:
                return miss("ungrounded" if not d.failed else "no_reply")
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
        first = not s.get("history") and not d.rounds  # all it read: the prefetch
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


class _Timed:
    """The Assistant's model, timed: every call counts and adds its ms to
    the `model` phase."""

    def __init__(self, inner: ChatModel, owner: "Assistant") -> None:
        self.inner, self.owner = inner, owner
        self.model = str(getattr(inner, "model", "") or "")

    def _note(self, start: float) -> None:
        t = self.owner.timing
        t["model"] = t.get("model", 0.0) + (time.monotonic() - start) * 1000
        self.owner.model_calls += 1

    def chat(self, messages: Sequence[Message], tools: Sequence[Any] = ()) -> Reply:
        start = time.monotonic()
        try:
            return self.inner.chat(messages, tools)
        finally:
            self._note(start)

    def stream(
        self, messages: Sequence[Message], tools: Sequence[Any] = ()
    ) -> Iterator[Reply]:
        start = time.monotonic()
        try:
            yield from self.inner.stream(messages, tools)
        finally:
            self._note(start)


def _route(state: State) -> str:
    return state.get("route", "ask")


def _last(today: date) -> str:
    n = today.year * 12 + today.month - 2
    return f"{n // 12}-{n % 12 + 1:02d}"


_NEW_TURN: Update = {
    "proposal_id": None,
    "evidence": [],
    "cost_pence": None,
    "payload_id": None,
    "turn_plan": None,
    "resolved": [],
    "chips": [],
}


def profile_context(
    conn: Connection, user_id: int, today: date
) -> tuple[str, list[ToolValue]]:
    """The L0 financial profile (#327): its prompt text (cached per Ledger
    version) and its figures for grounding."""
    text, prof = profile.profile_context(conn, user_id, today)
    return text, tool_values("profile", prof)


NO_ADVICE = (
    "I couldn't check an answer to that, and your Guidance and Insights have"
    " nothing on it yet."
)
_AGAIN = object()  # a parallel read to run again on the turn's connection


def _pinned(call: Any, message: str, today: date) -> Any:
    """A spend query's range is the period code read in the message, never the
    model's reading of it ("this year" is 1 January to today)."""
    p = resolve.one_period(message, today)
    if p is None or getattr(call, "name", "") != "spend_for_query":
        return call
    args = {**call.arguments, "since": f"{p.since:%Y-%m}", "until": f"{p.until:%Y-%m}"}
    return call.model_copy(update={"arguments": args})


def miss(reason: str) -> Update:
    """No plan fits and no checked reply: say so and offer what does work."""
    MISSES[reason] += 1
    log.info("assistant miss: %s", reason)  # a fixed code, never the question
    return {"reply": MISS, "path": "miss", "chips": list(MISS_CHIPS), "evidence": []}


def _turn_plan(state: State) -> planner.Plan | None:
    raw = state.get("turn_plan")
    try:
        return planner.Plan.model_validate(raw) if raw else None
    except ValidationError:
        return None


def _shown_evidence(ids: Any) -> list[str]:
    """Evidence IDs for the panel: the resolver's own values are not one."""
    return sorted({e for e in ids if e != resolve.EVIDENCE})


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
    cost_pence: int | None = None  # a cloud call this turn (#301)
    payload_id: str | None = None
    counted: tuple[Evidence, ...] = ()  # Transactions behind its totals
    resolved: tuple[str, ...] = ()  # what code resolved the question to (#317)
    chips: tuple[str, ...] = ()
    # server-side monotonic ms: total and per phase, model calls and name
    timing: dict[str, Any] = field(default_factory=dict[str, Any])


def _timing(a: Assistant, started: float) -> dict[str, Any]:
    total = (time.monotonic() - started) * 1000
    phases = {k: round(v, 1) for k, v in a.timing.items()}
    phases["other"] = round(max(total - sum(a.timing.values()), 0.0), 1)
    model = a.model.model if isinstance(a.model, _Timed) else ""
    return {
        "total_ms": round(total, 1),
        "phases": phases,
        "model_calls": a.model_calls,
        "model": model if a.model_calls else None,
    }


def _answer(
    assistant: Assistant, thread: str, out: dict[str, Any], started: float
) -> Answer:
    pending = out.get("__interrupt__") or ()
    evidence = tuple(out.get("evidence") or ())
    return Answer(
        thread_id=thread,
        turn_id=out.get("turn_id", ""),
        route=out.get("route", "ask"),
        reply=out.get("reply", ""),
        path=out.get("path", "deterministic"),
        evidence=evidence,
        proposal_id=out.get("proposal_id") if pending else None,
        cost_pence=out.get("cost_pence"),
        payload_id=out.get("payload_id"),
        counted=tuple(assistant.counted[e] for e in evidence if e in assistant.counted),
        resolved=tuple(out.get("resolved") or ()),
        chips=tuple(out.get("chips") or ()),
        timing=_timing(assistant, started),
    )


def ask(
    assistant: Assistant,
    message: str,
    thread_id: str | None = None,
    cloud: bool | None = None,
) -> Answer:
    """Run one turn on a thread (a new one when `thread_id` is None). `cloud`
    sets the thread's opt-in; None keeps it (off on a new thread)."""
    thread = thread_id or uuid.uuid4().hex
    turn: dict[str, Any] = {
        "message": message,
        "turn_id": uuid.uuid4().hex,
        "today": assistant.today.isoformat(),
        "thread_id": thread,
    }
    if cloud is not None:
        turn["cloud"] = cloud
    started = time.monotonic()
    out = assistant.invoke(turn, thread)
    return _answer(assistant, thread, out, started)


def waiting_for(assistant: Assistant, thread_id: str) -> int | None:
    """The Proposal the thread is paused on, if any."""
    snap = assistant.compile().get_state({"configurable": {"thread_id": thread_id}})
    if not snap.interrupts:
        return None
    return cast(dict[str, int], snap.interrupts[0].value)["proposal_id"]


def resume(
    assistant: Assistant, thread_id: str, decision: Literal["confirm", "reject"]
) -> Answer:
    started = time.monotonic()
    out = assistant.invoke(Command(resume=decision), thread_id)
    return _answer(assistant, thread_id, out, started)
