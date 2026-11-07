"""The Subscription registry (#103): one entry per Merchant with a reason, user
decisions that detection never overwrites, and negative entries."""

import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.ledger import (
    DETECTED_REASONS,
    DETECTED_STATUSES,
    INTERVALS,
    InvalidEntry,
    Ledger,
    UnknownTarget,
)
from finance_agent.ledger.taxonomy import TAXONOMY
from finance_agent.storage import open_db, schema

LedgerFor = Callable[[int], Ledger]


def merchant(led: Ledger, name: str) -> int:
    return led._db.insert(schema.merchant, name=name)  # pyright: ignore[reportPrivateUsage]


def test_a_user_added_subscription_is_remembered_with_its_reason(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH STREAM")

    led.add_subscription(m, "subscription", "Streaming")

    (e,) = led.subscriptions()
    assert (e.merchant_id, e.merchant, e.kind, e.group) == (
        m,
        "SYNTH STREAM",
        "subscription",
        "Streaming",
    )
    assert (e.status, e.reason, e.source) == ("active", "user_added", "user")
    assert e.evidence_on == date.today()
    assert e.created_at is not None


def test_removing_an_entry_remembers_it_as_not_a_subscription(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH GYM")
    led.add_subscription(m, "subscription", "Memberships")

    led.not_a_subscription(m)

    (e,) = led.subscriptions()
    assert (e.status, e.kind, e.group, e.reason, e.source) == (
        "not_subscription",
        None,
        None,
        None,
        "user",
    )
    # the negative entry stops detection from listing it again
    assert not led.record_detection(
        m, "subscription", "Memberships", "pattern", "active", date(2026, 1, 5), 3
    )
    assert led.subscription(m) == e


def test_regroup_keeps_the_reason_and_confirm_records_the_user(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH CLOUD")
    assert led.record_detection(
        m, "subscription", "Software", "pattern", "suggested", date(2026, 3, 1), 4,
        "monthly",
    )  # fmt: skip

    led.regroup_subscription(m, "subscription", "Cloud storage")
    e = led.subscription(m)
    assert e is not None
    assert (e.group, e.reason, e.status, e.charge_count, e.interval) == (
        "Cloud storage",
        "pattern",
        "suggested",
        4,
        "monthly",
    )

    led.confirm_subscription(m)
    e2 = led.subscription(m)
    assert e2 is not None
    assert (e2.status, e2.reason, e2.source, e2.evidence_on) == (
        "active",
        "user_confirmed",
        "user",
        date.today(),
    )
    assert e2.created_at == e.created_at <= e2.changed_at


def test_a_bill_group_must_come_from_bills(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH ENERGY")
    with pytest.raises(InvalidEntry):
        led.add_subscription(m, "bill", "Streaming")
    with pytest.raises(InvalidEntry):
        led.add_subscription(m, "subscription", "Energy")
    led.add_subscription(m, "bill", "Energy")
    with pytest.raises(InvalidEntry):  # detection cannot claim a user's reason
        led.record_detection(m, "bill", "Energy", "user_added", "active", date.today())
    with pytest.raises(UnknownTarget):  # nothing to regroup
        led.regroup_subscription(merchant(led, "SYNTH NEW"), "bill", "Water")


def test_every_change_bumps_the_ledger_version(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH MUSIC")
    v = led.ledger_version()
    led.add_subscription(m, "subscription", "Streaming")
    led.not_a_subscription(m)
    assert led.ledger_version() == v + 2


def test_rerunning_detection_is_idempotent(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH AI")
    args = ("subscription", "AI tools", "known_service", "active", date(2026, 2, 1))
    assert led.record_detection(m, *args)
    v = led.ledger_version()
    assert not led.record_detection(m, *args)
    assert led.ledger_version() == v


GROUPS = [("subscription", g) for g in TAXONOMY["Subscriptions"]] + [
    ("bill", g) for g in TAXONOMY["Bills"]
]
DETECTION = st.tuples(
    st.sampled_from(GROUPS),
    st.sampled_from(DETECTED_REASONS),
    st.sampled_from(DETECTED_STATUSES),
    st.dates(date(2024, 1, 1), date(2027, 1, 1)),
    st.none() | st.integers(1, 60),
    st.none() | st.sampled_from(INTERVALS),
)


@settings(max_examples=60, deadline=None)
@given(
    before=st.lists(DETECTION, max_size=3),
    action=st.sampled_from(["add", "regroup", "confirm", "remove"]),
    group=st.sampled_from(GROUPS),
    after=st.lists(DETECTION, min_size=1, max_size=5),
)
def test_detection_never_overrides_a_user_decision(
    before: list[Any], action: str, group: tuple[str, str], after: list[Any]
) -> None:
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            m = merchant(led, "SYNTH SERVICE")
            for (k, g), *rest in before:
                led.record_detection(m, k, g, *rest)
            if action in ("regroup", "confirm") and led.subscription(m) is None:
                action = "add"
            {
                "add": lambda: led.add_subscription(m, *group),
                "regroup": lambda: led.regroup_subscription(m, *group),
                "confirm": lambda: led.confirm_subscription(m, *group),
                "remove": lambda: led.not_a_subscription(m),
            }[action]()
            decided = led.subscription(m)
            version = led.ledger_version()

            for (k, g), *rest in after:
                assert not led.record_detection(m, k, g, *rest)

            assert led.subscription(m) == decided
            assert decided is not None and decided.source == "user"
            assert led.ledger_version() == version
        engine.dispose()


def test_another_user_can_neither_see_nor_touch_my_entries(
    ledger_for: LedgerFor,
) -> None:
    mine, theirs = ledger_for(1), ledger_for(2)
    m = merchant(mine, "SYNTH STREAM")
    mine.add_subscription(m, "subscription", "Streaming")
    before = mine.subscriptions()
    merchant(theirs, "SYNTH STREAM")  # same name, their own Merchant id

    assert theirs.subscriptions() == []
    assert theirs.subscription(m) is None
    for attempt in (
        lambda: theirs.add_subscription(m, "subscription", "Software"),
        lambda: theirs.regroup_subscription(m, "subscription", "Software"),
        lambda: theirs.confirm_subscription(m),
        lambda: theirs.not_a_subscription(m),
        lambda: theirs.record_detection(
            m, "subscription", "Software", "pattern", "active", date.today()
        ),
    ):
        with pytest.raises(UnknownTarget):
            attempt()
    assert mine.subscriptions() == before
    assert theirs.subscriptions() == []
