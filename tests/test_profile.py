"""The L0 profile (#327 part 1) on a synthetic Persona: grounded, small,
deterministic, never stale, and built without the Keychain or a log of figures."""

import logging
import shutil
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import keyring
import pytest
from sqlalchemy import Engine

from build_demo_db import build as build_personas
from finance_agent.assistant import context
from finance_agent.assistant.grounding import check_grounding
from finance_agent.assistant.values import tool_values
from finance_agent.demo.session import plain_engine
from finance_agent.engine import profile as pf
from finance_agent.identity import current_user
from finance_agent.ledger import Categorisation, Ledger, Transaction
from finance_agent.ledger.proposals import MemoryDraft, Proposals, Provenance

TODAY = date(2026, 11, 8)
UID = current_user()


@pytest.fixture(scope="module")
def persona(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    held = keyring.get_keyring()
    root = tmp_path_factory.mktemp("personas")
    build_personas(root, histories=1, months=13, today=TODAY)
    keyring.set_keyring(held)
    yield root / "personas" / "0.db"


@pytest.fixture
def engine(
    persona: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """A copy of the Persona; from here on any Keychain call fails the test."""

    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("the profile touched the Keychain")

    for name in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, name, refuse)
    path = tmp_path / "p.db"
    shutil.copyfile(persona, path)
    pf.forget()
    eng = plain_engine(path)
    yield eng
    eng.dispose()
    pf.forget()


def test_every_figure_in_the_text_grounds_and_it_stays_small(engine: Engine) -> None:
    with engine.begin() as conn:
        text, p = pf.profile_context(conn, UID, TODAY)
    assert check_grounding(text, tool_values("profile", p), today=TODAY).grounded
    assert len(text) <= 800 * context.CHARS_PER_TOKEN
    assert p.accounts and p.bills and p.categories and p.income
    assert p.months == 13 and p.first_month == "2025-11" and p.last_month == "2026-11"


def test_it_is_deterministic(engine: Engine) -> None:
    with engine.begin() as conn:
        a = pf.render(pf.build(conn, UID, TODAY))
        b = pf.render(pf.build(conn, UID, TODAY))
    assert a == b


def test_a_correction_is_never_served_stale(engine: Engine) -> None:
    with engine.begin() as conn:
        led = Ledger(conn, UID)
        text, p = pf.profile_context(conn, UID, TODAY)
        assert pf.profile_context(conn, UID, TODAY)[1] is p  # cached while unchanged
        recent = [
            t
            for t in led.transactions()
            if t.categorisation
            and t.categorisation.kind == "Spending"
            and t.posted_on.month == 10
        ]
        for t in recent:  # all of October's spending becomes Shopping
            led.correct_transaction(t.id, ("Shopping", "Online"))
        text2, p2 = pf.profile_context(conn, UID, TODAY)
    assert p2 is not p and p2.evidence_id != p.evidence_id and text2 != text
    shop = {c.name: c for c in p2.categories}["Shopping"]
    assert (
        shop.median_3m.minor
        > {c.name: c for c in p.categories}["Shopping"].median_3m.minor
    )


def test_a_new_memory_shows_without_its_text(engine: Engine) -> None:
    with engine.begin() as conn:
        _, before = pf.profile_context(conn, UID, TODAY)
        props = Proposals(conn, UID)
        said = Provenance(turn_id="t1", quote="save for a bike", said_on=TODAY)
        draft = MemoryDraft(
            type="goal", text="Save for a bike", tags=("bike",), provenance=said
        )
        props.confirm(props.propose_memory(draft).id)
        mid = props.memories()[0].id  # newest first
        text, after = pf.profile_context(conn, UID, TODAY)
    assert mid in {g.memory_id for g in after.goals} - {
        g.memory_id for g in before.goals
    }
    assert "bike" in text and "Save for a bike" not in text


def _spend(tid: int, on: date, pence: int, merchant_id: int = 1) -> Transaction:
    c = Categorisation(
        "Spending", "SYNTH SHOP", ("Shopping", "Online"), "default", 1, merchant_id
    )
    return Transaction(
        on,
        "SYNTH SHOP",
        Decimal(-pence).scaleb(-2),
        None,
        frozenset(),
        tid,
        categorisation=c,
    )


def test_unusual_payments_are_large_against_the_merchant_or_a_large_first() -> None:
    old = [_spend(i, TODAY - timedelta(days=90 + i), 2_000) for i in range(1, 6)]
    rows = [
        *old,
        _spend(10, TODAY - timedelta(days=3), 6_000),  # 3x the usual 20.00: unusual
        _spend(11, TODAY - timedelta(days=2), 5_900),  # under 3x: not
        _spend(12, TODAY - timedelta(days=40), 9_000),  # too long ago
        _spend(13, TODAY - timedelta(days=1), 10_000, merchant_id=2),  # a large first
        _spend(14, TODAY - timedelta(days=1), 9_999, merchant_id=3),  # a first, smaller
    ]
    got = pf._unusual(rows, TODAY)  # pyright: ignore[reportPrivateUsage]
    assert [u.transaction_id for u in got] == [13, 10]


@pytest.mark.parametrize(
    ("m3", "m12", "want"),
    [
        (12_500, 10_000, "rising"),
        (12_400, 10_000, None),
        (7_500, 10_000, "falling"),
        (2_500, 2_000, None),
        (0, 1_999, None),
    ],
)
def test_trends_need_a_quarter_and_twenty_pounds(
    m3: int, m12: int, want: str | None
) -> None:
    c = pf.CategoryFact(
        name="X", median_3m=pf.Amount(minor=m3), median_12m=pf.Amount(minor=m12)
    )
    assert pf._direction(c) == want  # pyright: ignore[reportPrivateUsage]


def test_money_reads_as_the_assistant_writes_it() -> None:
    assert pf.money(pf.Amount(minor=123456)) == "£1,234.56"
    assert pf.money(pf.Amount(minor=-500)) == "-£5.00"
    assert pf.money(pf.Amount(minor=1200, currency="EUR")) == "12.00 EUR"


def test_the_refresh_logs_no_figures_or_names(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="finance_agent.engine.profile")
    pf.refresh_quietly(engine, UID)
    with engine.begin() as conn:
        text, _ = pf.profile_context(conn, UID)
    logged = caplog.text
    assert "profile ready" in logged and "£" not in logged
    assert not any(
        word in logged
        for word in text.split()
        if len(word) > 4 and word.isalpha() and word.isupper()
    )


def test_a_failed_refresh_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    class Broken:
        def begin(self) -> None:
            raise RuntimeError("secret detail")

    pf.refresh_quietly(Broken(), UID)  # type: ignore[arg-type]
    assert "secret detail" not in caplog.text
