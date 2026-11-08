"""Semantic resolution (#317): Merchants filed elsewhere count for the
Category they mean, and a term no resolver reads finds its Category."""

import json
import time
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import pytest
from pydantic import JsonValue
from sqlalchemy import Engine

from finance_agent.assistant import FakeChatModel, Reply, ToolCall, semantic
from finance_agent.assistant.graph import Assistant, ask
from finance_agent.categorise import embedding, zero_shot
from finance_agent.categorise.embedding import Vector
from finance_agent.ledger import Ledger
from synthetic_assistant import DECEMBER, ME, ROWS, seed

TODAY = date(2026, 1, 20)
CATS = sorted(zero_shot.DESCRIPTIONS)
FUEL = CATS.index(("Transport", "Fuel"))
DIMS = len(CATS) + 1
FUELISH = ("fuel", "petrol", "forecourt", "garage", "shell", "pfs")


class Words:
    """A fake embedder: a description is its own axis; a name or term with a
    fuel word lies on Fuel's axis; anything else on a spare axis."""

    model = "nomic-embed-text"

    def digest(self) -> str | None:
        return ""

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        out: list[Vector] = []
        for t in texts:
            v = [0] * DIMS
            if t in zero_shot.DESCRIPTIONS.values():
                v[
                    CATS.index(
                        next(c for c, d in zero_shot.DESCRIPTIONS.items() if d == t)
                    )
                ] = 1
            elif any(w in t.casefold() for w in FUELISH):
                v[FUEL] = 1
            else:
                v[-1] = 1
            out.append(embedding.quantise(v))
        return out


@pytest.fixture
def seeded(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    rows = [*DECEMBER, (-12, "DEB", "WAYSIDE GARAGE", -2000), *ROWS]
    seed(engine, ME, tmp_path, rows)
    monkeypatch.setattr(semantic, "EMBEDDER", Words)
    monkeypatch.setattr(zero_shot, "_cache", {})
    monkeypatch.setattr(semantic, "_index", {})
    monkeypatch.setattr(semantic, "_queries", {})
    with engine.begin() as conn:
        embedding.refresh(conn, ME, Words())
    semantic.refresh_quietly(engine, ME)
    yield engine


def test_the_thresholds_are_the_evals_swept_ones() -> None:
    results = (
        Path(__file__).parents[1] / "evals" / "results" / "semantic_resolution.json"
    )
    swept = json.loads(results.read_text())["thresholds"]
    assert {m: t.__dict__ for m, t in semantic.CALIBRATED.items()} == swept


def test_rrf_rewards_agreement() -> None:
    assert semantic.rrf([1, 2, 3], [3, 4]) == [3, 1, 2, 4]
    assert semantic.rrf() == []


def test_fuel_counts_a_petrol_station_filed_elsewhere_and_says_so(
    seeded: Engine,
) -> None:
    model = FakeChatModel([])
    with seeded.begin() as conn:
        led = Ledger(conn, ME)
        garage = next(m for m in led.merchants() if "garage" in m.name.casefold())
        assert garage.default_category != ("Transport", "Fuel")  # filed elsewhere
        a = ask(
            Assistant(model, conn, TODAY, prefetch=True),
            "How much did I spend on fuel in December 2025?",
        )
    assert model.calls == []
    assert a.path == "planner"
    # Shell £48.00 + Tesco PFS £36.00 + Wayside Garage £20.00
    assert a.reply.startswith("You spent £104.00 on Fuel in December 2025.")
    assert "That includes Wayside Garage" in a.reply and "filed under" in a.reply
    for ev in a.counted:
        assert sum(t.amount_minor for t in ev.transactions) == ev.total_minor


def test_a_term_no_resolver_reads_finds_its_category(seeded: Engine) -> None:
    with seeded.begin() as conn:
        a = ask(
            Assistant(FakeChatModel([]), conn, TODAY, prefetch=True),
            "How much did I spend on forecourt in December 2025?",
        )
    assert a.path == "planner"
    assert "£104.00" in a.reply
    assert any("for “forecourt”" in lb for lb in a.resolved)


def test_a_lookup_is_fast_and_cached(seeded: Engine) -> None:
    with seeded.begin() as conn:
        led = Ledger(conn, ME)
        semantic.term(led, "forecourt")  # embeds once
        t0 = time.perf_counter()
        found = semantic.term(led, "forecourt")
        ms = (time.perf_counter() - t0) * 1000
    assert found is not None and found.category == ("Transport", "Fuel")
    assert ms < 5


def test_with_ollama_off_nothing_is_added(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(engine, ME, tmp_path, [*DECEMBER, (-12, "DEB", "WAYSIDE GARAGE", -2000)])
    monkeypatch.setattr(semantic, "_index", {})
    monkeypatch.setattr(zero_shot, "_cache", {})
    with engine.begin() as conn:
        a = ask(
            Assistant(FakeChatModel([]), conn, TODAY, prefetch=True),
            "How much did I spend on fuel in December 2025?",
        )
    assert "That includes" not in a.reply


def test_the_model_never_reads_a_period_code_already_did(seeded: Engine) -> None:
    """The real 4b read "this year" as since January 2025 (#317)."""
    wrong: dict[str, JsonValue] = {
        "merchants": ["Tesco"],
        "since": "2025-01",
        "until": "2026-01",
    }
    q = "How much did I spend at Tesco this year, the shop and the fuel?"
    model = FakeChatModel(
        [
            Reply(content="ask"),
            Reply(tool_calls=(ToolCall(name="spend_for_query", arguments=wrong),)),
            Reply(content="Done."),
        ]
    )
    with seeded.begin() as conn:
        ask(Assistant(model, conn, TODAY, prefetch=True), q)
    tool_msgs = [m for msgs, _ in model.calls for m in msgs if m.role == "tool"]
    sent = json.loads(tool_msgs[-1].content)
    assert (sent["since"], sent["until"]) == ("2026-01", "2026-01")
