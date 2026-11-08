"""#215 F: the merchant-level local LLM classifier. One call per Merchant to
localhost Ollama, think off and temperature 0, its reply constrained to the
taxonomy's sub-Categories or "unsure". It sees the payee, description tokens,
amount *patterns in words* and the nearest labelled Merchants, never a raw
amount. Synthetic payees only."""

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from finance_agent import categorise
from finance_agent.categorise import llm
from finance_agent.categorise.embedding import EmbeddingUnavailable
from test_categorise_embedding import FakeConnection

FUEL = ("Transport", "Fuel")
COFFEE = ("Food & Drink", "Coffee")
PAYEE = llm.Payee(
    name="QQV HILLTOP SERVICES",
    descriptions=("QQV HILLTOP SERVICES 0042", "QQV HILLTOP SERVICES"),
    payments=(
        (date(2026, 1, 3), Decimal("-61.37")),
        (date(2026, 2, 7), Decimal("-58.12")),
        (date(2026, 3, 7), Decimal("-63.90")),
    ),
    examples=(("ZZQ RIDGEWAY FORECOURT", FUEL), ("BEAN THERE", COFFEE)),
)


def _reply(category: str) -> tuple[int, Any]:
    return 200, {
        "message": {"role": "assistant", "content": json.dumps({"category": category})}
    }


def _classifier(
    *replies: tuple[int, Any],
) -> tuple[llm.OllamaClassifier, FakeConnection]:
    fake = FakeConnection(list(replies))
    return llm.OllamaClassifier(connect=fake), fake


def test_a_reply_names_one_sub_category() -> None:
    c, _ = _classifier(_reply("Transport › Fuel"))
    assert c.classify(PAYEE) == FUEL


def test_the_request_is_greedy_with_thinking_off_and_a_closed_enum() -> None:
    c, fake = _classifier(_reply("unsure"))
    c.classify(PAYEE)
    method, path, body = fake.sent[0]
    assert (method, path) == ("POST", "/api/chat")
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    allowed = body["format"]["properties"]["category"]["enum"]
    leaves = {
        f"{top} › {sub}"
        for top, subs in categorise.TAXONOMY.items()
        if top != categorise.UNCATEGORISED[0]
        for sub in subs
    }
    assert set(allowed) == leaves | {"unsure"}


def test_the_prompt_carries_patterns_in_words_and_never_an_amount() -> None:
    c, fake = _classifier(_reply("unsure"))
    c.classify(PAYEE)
    prompt = " ".join(m["content"] for m in fake.sent[0][2]["messages"])
    assert "QQV HILLTOP SERVICES" in prompt
    assert "ZZQ RIDGEWAY FORECOURT" in prompt and "Transport › Fuel" in prompt
    assert "monthly" in prompt
    for raw in ("61.37", "58.12", "63.90", "6137", "-61"):
        assert raw not in prompt


@pytest.mark.parametrize(
    "content",
    ["unsure", "Transport", "Transport › Rockets", "Uncategorised › Uncategorised"],
)
def test_unsure_or_anything_outside_the_taxonomy_is_no_answer(content: str) -> None:
    c, _ = _classifier(_reply(content))
    assert c.classify(PAYEE) is None


@pytest.mark.parametrize(
    "reply",
    [
        (200, {"message": {"content": "not json"}}),
        (200, {"message": {"content": json.dumps(["Transport › Fuel"])}}),
        (200, {"nothing": 1}),
    ],
)
def test_a_malformed_reply_is_no_answer(reply: tuple[int, Any]) -> None:
    c, _ = _classifier(reply)
    assert c.classify(PAYEE) is None


def test_ollama_down_raises_unavailable() -> None:
    c, _ = _classifier((500, {}))
    with pytest.raises(EmbeddingUnavailable):
        c.classify(PAYEE)


def test_only_localhost_ollama_is_dialled() -> None:
    with pytest.raises(ValueError):
        llm.OllamaClassifier(base_url="http://example.com:11434")


@pytest.mark.parametrize(
    ("payments", "words"),
    [
        (
            [(date(2026, m, 1), Decimal("-9.99")) for m in range(1, 7)],
            ["£5 to £20", "monthly"],
        ),
        (
            [(date(2026, 1, d), Decimal("-3.10")) for d in (5, 12, 19, 26)],
            ["under £5", "weekly"],
        ),
        ([(date(2026, 1, 3), Decimal("-450"))], ["over £200", "one payment"]),
        (
            [(date(2026, 1, 3), Decimal("-30")), (date(2026, 1, 4), Decimal("-40"))],
            ["£20 to £60", "mostly weekends"],
        ),
    ],
)
def test_amount_patterns_are_words(
    payments: list[tuple[date, Decimal]], words: list[str]
) -> None:
    text = llm.pattern(payments)
    for w in words:
        assert w in text
