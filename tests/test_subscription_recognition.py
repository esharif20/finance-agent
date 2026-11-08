"""Subscription recognition (#104): known services from the first charge,
descriptor patterns asked about straight away, recurring detection; written
through `record_detection`, so user decisions always win."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.categorise.subscriptions import (
    KNOWN_SERVICES,
    known_service,
    pattern_group,
)
from finance_agent.ledger import REGISTRY_KINDS, Ledger, SubscriptionEntry
from finance_agent.ledger.taxonomy import TAXONOMY

Export = Callable[[str], Path]


def test_every_known_service_maps_to_a_v2_subscriptions_or_bills_leaf() -> None:
    assert KNOWN_SERVICES
    for name, (kind, group) in KNOWN_SERVICES.items():
        assert name.strip() == name and name
        assert group in TAXONOMY[REGISTRY_KINDS[kind]], name


def test_known_services_match_their_merchant_names() -> None:
    assert known_service("NETFLIX.COM") == ("Netflix", "subscription", "Streaming")
    assert known_service("Spotify") == ("Spotify", "subscription", "Streaming")
    assert known_service("DISNEY PLUS") is not None
    assert known_service("NETFLIX FAN SHOP") is None
    assert known_service("NETFLİX") is None  # dotted capital I
    assert known_service("NETFLIXѕ") is None  # Cyrillic dze tail


def test_descriptor_patterns() -> None:
    assert pattern_group("SYNTH CLUB MEMBERSHIP 0412") == "Memberships"
    assert pattern_group("SYNTHAPP SUBSCRIPTION") == "Other subscriptions"
    assert pattern_group("APPLE.COM/BILL") == "Other subscriptions"
    assert pattern_group("SYNTH CAFE 0042") is None


def hsbc(rows: list[tuple[date, str, str]]) -> str:
    return "".join(f"{d:%d/%m/%Y},{desc},{amt}\n" for d, desc, amt in rows)


def recognise(
    engine: Engine, export: Export, rows: list[tuple[date, str, str]]
) -> list[SubscriptionEntry]:
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([export(hsbc(rows))])
        categorise.run(conn, 1)
        return Ledger(conn, 1).subscriptions()


def months(n: int, day: int = 3) -> list[date]:
    return [date(2026, 1 + i, day) for i in range(n)]


def by_name(entries: list[SubscriptionEntry]) -> dict[str, SubscriptionEntry]:
    return {e.merchant: e for e in entries}


def test_a_known_service_is_likely_from_its_first_charge(
    engine: Engine, write_export: Export
) -> None:
    e = by_name(
        recognise(engine, write_export, [(date(2026, 1, 3), "NETFLIX.COM", "-10.99")])
    )
    x = e["Netflix"]
    assert (x.kind, x.group, x.reason, x.status, x.source) == (
        "subscription",
        "Streaming",
        "known_service",
        "likely",
        "detection",
    )
    assert x.evidence_on == date(2026, 1, 3)


def test_a_descriptor_pattern_is_likely_straight_away(
    engine: Engine, write_export: Export
) -> None:
    e = by_name(
        recognise(
            engine,
            write_export,
            [(date(2026, 1, 3), "SYNTH CLUB MEMBERSHIP", "-30.00")],
        )
    )
    x = e["SYNTH CLUB MEMBERSHIP"]
    assert (x.group, x.reason, x.status) == ("Memberships", "pattern", "likely")


def test_an_unknown_monthly_charge_is_suggested_with_its_evidence(
    engine: Engine, write_export: Export
) -> None:
    rows = [(d, "SYNTH VIDEO CO", "-7.99") for d in months(3)]
    x = by_name(recognise(engine, write_export, rows))["SYNTH VIDEO CO"]
    assert (x.kind, x.group, x.reason, x.status) == (
        "subscription",
        "Other subscriptions",
        "pattern",
        "suggested",
    )
    assert (x.charge_count, x.interval, x.evidence_on) == (3, "monthly", months(3)[-1])


def test_a_recurring_bill_keeps_its_bills_group(
    engine: Engine, write_export: Export
) -> None:
    rows = [(d, "SYNTH LETTINGS RENT", "-900.00") for d in months(3)]
    x = by_name(recognise(engine, write_export, rows))["SYNTH LETTINGS RENT"]
    assert (x.kind, x.group) == ("bill", "Rent or mortgage")


def test_adversarial_a_one_off_lookalike_is_not_listed(
    engine: Engine, write_export: Export
) -> None:
    rows = [
        (date(2026, 1, 3), "NETFLIX FAN SHOP", "-25.00"),
        (date(2026, 1, 4), "NETFLİX", "-10.99"),
        (date(2026, 1, 5), "SYNTH CAFE", "-3.20"),
    ]
    assert recognise(engine, write_export, rows) == []


def test_adversarial_drift_and_refunds(engine: Engine, write_export: Export) -> None:
    pence = [1000 + 60 * i for i in range(6)]  # +6% a month
    drift = [
        (d, "SYNTH DRIFT", f"-{p // 100}.{p % 100:02d}")
        for d, p in zip(months(6), pence, strict=True)
    ]
    refunds = [(d, "SYNTH REFUNDER REFUND", "7.99") for d in months(4, day=10)]
    one_charge = [(date(2026, 2, 3), "SYNTH REFUNDER", "-7.99")]
    assert recognise(engine, write_export, drift + refunds + one_charge) == []


def test_rerunning_recognition_writes_nothing_new(
    engine: Engine, write_export: Export
) -> None:
    rows = [(d, "SYNTH VIDEO CO", "-7.99") for d in months(3)]
    rows.append((date(2026, 1, 3), "NETFLIX.COM", "-10.99"))
    first = recognise(engine, write_export, rows)
    with engine.begin() as conn:
        categorise.run(conn, 1)
        assert Ledger(conn, 1).subscriptions() == first


DECISIONS = st.sampled_from(["not", "add", "confirm"])


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    DECISIONS,
    st.sampled_from(["NETFLIX.COM", "SYNTH VIDEO CO", "SYNTH GYM MEMBERSHIP"]),
)
def test_recognition_never_overwrites_a_user_decision(
    tmp_path_factory: pytest.TempPathFactory, decision: str, name: str
) -> None:
    from finance_agent.storage import open_db

    tmp = tmp_path_factory.mktemp("db")
    engine = open_db(tmp / "ledger.db")
    n = 0

    def export(text: str) -> Path:
        nonlocal n
        n += 1
        p = tmp / f"e{n}.csv"
        p.write_text(text)
        return p

    try:
        rows = [(d, name, "-7.99") for d in months(3)]
        (e,) = recognise(engine, export, rows)
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            if decision == "not":
                led.not_a_subscription(e.merchant_id)
            elif decision == "add":
                led.add_subscription(e.merchant_id, "bill", "Insurance")
            else:
                led.confirm_subscription(e.merchant_id, "subscription", "Software")
            decided = led.subscription(e.merchant_id)
        later = [(d + timedelta(days=1), name, "-7.99") for d in months(4)[1:]]
        assert by_name(recognise(engine, export, later))[e.merchant] == decided
    finally:
        engine.dispose()


SEPARATORS = st.sampled_from(list("&-_*./+'|\x00"))


@given(st.sampled_from(sorted(KNOWN_SERVICES)), SEPARATORS, st.data())
def test_adversarial_punctuation_inside_a_name_never_matches(
    name: str, sep: str, data: st.DataObject
) -> None:
    # "B & T" or "E-E" is another business's initials, not BT or EE
    letters = name.replace(" ", "")
    at = data.draw(st.integers(1, len(letters) - 1))
    assert known_service(letters[:at] + sep + letters[at:]) is None


def test_spacing_and_web_suffixes_still_match() -> None:
    for n in ("DISNEYPLUS", "disney  plus", "NOWTV", "BT.COM", "Netflix.co.uk"):
        assert known_service(n) is not None, n
    for n in ("B & T", "B T", "S*K*Y", "T.V. LICENCE", "NETFLIX\x00"):
        assert known_service(n) is None, n


def test_noisy_bank_text_still_names_the_known_service() -> None:
    # synthetic descriptors: SimpleFIN joins payee and description (#210)
    claude = ("Anthropic", "subscription", "AI tools")
    assert known_service("ANTHROPIC CLAUDE SUB ANTHROPIC CLAUDE") == claude
    assert known_service("OPENAI CHATGPT SUBSCR OPENAI CHATGPT SU") is not None
    assert known_service("NETFLIX LTD 4417") == ("Netflix", "subscription", "Streaming")
    # cleaning only drops noise: other words still block a match
    assert known_service("NETFLIX FAN SHOP LTD") is None
    assert known_service("CLAUDE SMITH PLUMBING SUB") is None
    assert known_service("NETFLİX LTD") is None  # non-ASCII still fails closed
