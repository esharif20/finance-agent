"""The answer cache (#140, ADR 0010): keyed by (intent, tool calls with
arguments, Ledger version, day), in the encrypted database, scoped to the user.
Any Ledger version change misses; only grounded Ask answers are stored."""

import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine, text

from finance_agent import api
from finance_agent.api import chat
from finance_agent.assistant import FakeChatModel, Reply, ToolCall
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.assistant.grounding import Money, format_figure
from finance_agent.ledger import Ledger
from finance_agent.ledger.answer_cache import AnswerCache, cache_key
from finance_agent.ledger.proposals import MemoryDraft, Proposals, Provenance
from finance_agent.storage import Scoped, open_db
from synthetic_assistant import EATING_OUT, ME, OTHER, seed

TODAY = date(2026, 1, 20)
MONTH = "2026-01"
CALLS = [{"name": "spending_summary", "arguments": {"month": MONTH}}]

# --- the key ---------------------------------------------------------------

json_args = st.dictionaries(
    st.text(max_size=5), st.one_of(st.integers(), st.text(max_size=5)), max_size=3
)
calls_st = st.lists(
    st.fixed_dictionaries({"name": st.text(max_size=8), "arguments": json_args}),
    max_size=3,
)


@given(
    intent=st.sampled_from(["ask", "plan", "memory", "correct"]),
    calls=calls_st,
    v=st.integers(0, 10**6),
    dv=st.integers(1, 10**6),
)
def test_a_different_ledger_version_is_always_a_different_key(
    intent: str, calls: list[dict[str, Any]], v: int, dv: int
) -> None:
    assert cache_key(intent, calls, v, TODAY) != cache_key(intent, calls, v + dv, TODAY)
    assert cache_key(intent, calls, v, TODAY) == cache_key(intent, calls, v, TODAY)


def test_every_part_of_the_key_counts() -> None:
    base = cache_key("ask", CALLS, 3, TODAY)
    other_month = [{"name": "spending_summary", "arguments": {"month": "2026-02"}}]
    assert (
        len(
            {
                base,
                cache_key("correct", CALLS, 3, TODAY),
                cache_key("ask", other_month, 3, TODAY),
                cache_key("ask", [], 3, TODAY),
                cache_key("ask", CALLS, 4, TODAY),
                cache_key("ask", CALLS, 3, TODAY + timedelta(days=1)),
            }
        )
        == 6
    )


# --- the store: the property --------------------------------------------------

DB = Path(tempfile.mkdtemp(prefix="fa-cache-"))
_engine: Engine | None = None


def shared() -> Engine:
    global _engine
    if _engine is None:
        _engine = open_db(DB / "ledger.db")
    return _engine


@settings(max_examples=60, deadline=None)
@given(calls=calls_st, bumps=st.integers(0, 4), reply=st.text(min_size=1))
def test_the_cache_never_serves_across_a_ledger_version_change(
    calls: list[dict[str, Any]], bumps: int, reply: str
) -> None:
    with shared().connect() as conn, conn.begin() as outer:
        cache = AnswerCache(conn, ME)
        cache.put("ask", calls, TODAY, reply)
        assert cache.get("ask", calls, TODAY) == reply
        for _ in range(bumps):
            Scoped(conn, ME).bump_ledger_version()  # an import or a Correction
        got = cache.get("ask", calls, TODAY)
        assert got == (reply if bumps == 0 else None)
        cache.put("ask", calls, TODAY, "fresh")  # storing drops the stale rows
        n = conn.execute(text("SELECT count(*) FROM answer_cache")).scalar()
        assert n == 1
        outer.rollback()


def test_another_users_cache_is_invisible(engine: Engine) -> None:
    with engine.begin() as conn:
        AnswerCache(conn, OTHER).put("ask", CALLS, TODAY, "theirs")
        mine = AnswerCache(conn, ME)
        assert mine.get("ask", CALLS, TODAY) is None
        assert mine.clear() == 0
        assert AnswerCache(conn, OTHER).get("ask", CALLS, TODAY) == "theirs"


def test_a_memory_change_clears_the_cache(engine: Engine) -> None:
    with engine.begin() as conn:
        cache, props = AnswerCache(conn, ME), Proposals(conn, ME)
        cache.put("ask", CALLS, TODAY, "cached")
        draft = MemoryDraft(
            type="goal",
            text="synthetic",
            provenance=Provenance(turn_id="t", quote="q", said_on=TODAY),
        )
        p = props.propose_memory(draft)
        assert cache.get("ask", CALLS, TODAY) == "cached"  # proposing is no change
        props.confirm(p.id)
        assert cache.get("ask", CALLS, TODAY) is None
        cache.put("ask", CALLS, TODAY, "cached")
        props.delete_memory(props.memories()[0].id)
        assert cache.get("ask", CALLS, TODAY) is None


# --- through the graph ---------------------------------------------------------


def pounds(minor: int) -> str:
    return format_figure(Money(minor=minor))


def first_round() -> Reply:
    return Reply(
        tool_calls=(ToolCall(name="spending_summary", arguments={"month": MONTH}),)
    )


def turn(engine: Engine, *script: Reply | str) -> tuple[Any, FakeChatModel]:
    m = FakeChatModel([Reply(content=s) if isinstance(s, str) else s for s in script])
    with engine.begin() as conn:
        return ask(Assistant(m, conn, TODAY), "what did I spend in January?"), m


@pytest.fixture
def total(engine: Engine, tmp_path: Path) -> int:
    seed(engine, OTHER, tmp_path)
    seed(engine, ME, tmp_path)
    with engine.begin() as conn:
        b = Ledger(conn, ME).category_breakdown(MONTH)
        return int(b.spending_after_refunds.scaleb(2))


def test_a_repeated_question_is_answered_from_the_cache_until_the_ledger_changes(
    engine: Engine, total: int
) -> None:
    answer = f"You spent {pounds(total)}."
    a, _ = turn(engine, "ask", first_round(), answer)
    assert a.path == "local_model"
    b, m = turn(engine, "ask", first_round())  # no third reply: never asked
    assert (b.path, b.reply) == ("cache", answer)
    assert len(m.calls) == 2
    with engine.begin() as conn:  # a Correction bumps the Ledger version
        led = Ledger(conn, ME)
        led.correct_transaction(led.transactions()[1].id, EATING_OUT)
    c, m = turn(engine, "ask", first_round(), answer)
    assert c.path == "local_model" and len(m.calls) == 3


def test_a_deterministic_fallback_is_never_cached(engine: Engine, total: int) -> None:
    wrong = f"You spent {pounds(total + 1)}."
    a, _ = turn(engine, "ask", first_round(), wrong, wrong)
    assert a.path == "deterministic"
    b, _ = turn(engine, "ask", first_round(), wrong, wrong)
    assert b.path == "deterministic"
    with engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM answer_cache")).scalar() == 0


def test_the_chat_api_says_the_cache_answered(engine: Engine, total: int) -> None:
    app = api.create_app(engine)
    app.dependency_overrides[chat.today] = lambda: TODAY
    answer = f"You spent {pounds(total)}."
    with TestClient(app) as c:
        for script, path in (
            (["ask", first_round(), answer], "local_model"),
            (["ask", first_round()], "cache"),
        ):
            m = FakeChatModel(
                [Reply(content=s) if isinstance(s, str) else s for s in script]
            )
            app.dependency_overrides[chat.chat_model] = lambda m=m: m  # pyright: ignore[reportUnknownLambdaType]
            r = c.post("/api/chat", json={"message": "spend in January?"})
            assert f'"path": "{path}"' in r.text


def test_cached_answers_are_encrypted_on_disk(
    engine: Engine, tmp_path: Path, total: int
) -> None:
    with engine.begin() as conn:
        AnswerCache(conn, ME).put("ask", CALLS, TODAY, "zebra-cached synthetic answer")
    assert b"zebra-cached" not in (tmp_path / "db" / "ledger.db").read_bytes()
