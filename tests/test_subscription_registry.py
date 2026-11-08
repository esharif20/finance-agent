"""The Subscription registry (#103): one entry per Merchant with a reason, user
decisions that detection never overwrites, and negative entries."""

import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.ledger import (
    DETECTED_REASONS,
    DETECTED_STATUSES,
    INTERVALS,
    InvalidEntry,
    Ledger,
    UnknownTarget,
    _Row,  # pyright: ignore[reportPrivateUsage]
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


def test_a_detection_row_after_a_user_decision_never_wins(
    ledger_for: LedgerFor,
) -> None:
    """Adversarial: a detection that lost a race with a user write (it checked
    before the user row landed) and shares its created_at must not become the
    entry. The read side holds the rule, not only record_detection."""
    led = ledger_for(1)
    m = merchant(led, "SYNTH RACE")
    led.not_a_subscription(m)
    decided = led.subscription(m)
    assert decided is not None
    led._registry(  # pyright: ignore[reportPrivateUsage]
        _Row(
            m, "detection", "subscription", "Streaming", "active", "pattern",
            date(2026, 1, 1),
        )
    )  # fmt: skip
    led._db.conn.execute(  # pyright: ignore[reportPrivateUsage]
        schema.subscription_entry.update().values(created_at=decided.changed_at)
    )

    assert led.subscription(m) == decided


USER_ACTION = st.sampled_from(["add", "regroup", "confirm", "remove"])


@settings(max_examples=60, deadline=None)
@given(
    steps=st.lists(
        st.tuples(
            st.sampled_from(["user", "raced", "revert"]),
            USER_ACTION,
            st.sampled_from(GROUPS),
            DETECTION,
        ),
        min_size=1,
        max_size=8,
    ),
    after=st.lists(DETECTION, min_size=1, max_size=4),
)
def test_detection_never_overrides_a_user_decision_with_reverts(
    steps: list[Any], after: list[Any]
) -> None:
    """#121's property with Review Queue undos (#110) mixed in: whenever any
    user decision is still in force, the entry is the latest such decision and
    neither a raced detection row nor a later detection replaces it."""
    with tempfile.TemporaryDirectory() as d:
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            m = merchant(led, "SYNTH SERVICE")
            live: list[tuple[int, Any]] = []  # (entry id, entry it made)
            for kind, action, group, ((k, g), *rest) in steps:
                if kind == "revert":
                    if live:
                        eid, _ = live.pop(len(live) // 2)
                        led.revert_subscription_decision(eid)
                    continue
                if kind == "raced":  # a detection that checked before the user
                    led._registry(_Row(m, "detection", k, g, *rest))  # pyright: ignore[reportPrivateUsage]
                    continue
                x = led.subscription(m)
                if action in ("regroup", "confirm") and (
                    x is None or x.status == "not_subscription"
                ):
                    action = "add"
                if action == "confirm":
                    eid = led.confirm_subscription(m, *group)
                elif action == "remove":
                    eid = led.not_a_subscription(m)
                else:  # add and regroup return nothing: the newest row is theirs
                    if action == "add":
                        led.add_subscription(m, *group)
                    else:
                        led.regroup_subscription(m, *group)
                    e = schema.subscription_entry
                    eid = conn.execute(sa.select(sa.func.max(e.c.id))).scalar_one()
                live.append((eid, led.subscription(m)))

            for (k, g), *rest in after:
                wrote = led.record_detection(m, k, g, *rest)
                if live:
                    assert not wrote
                else:  # it writes, unless it repeats the latest entry exactly
                    x = led.subscription(m)
                    assert x is not None
                    now = (x.kind, x.group, x.reason, x.status, x.evidence_on)
                    assert now + (x.charge_count, x.interval) == (k, g, *rest)
                    assert x.source == "detection"
            if live:
                e = led.subscription(m)
                assert e is not None and e.source == "user"
                # status_since is history-derived: reverting an earlier entry
                # legitimately moves where this status began, so it is not
                # part of "the latest live decision" (not a clock issue)
                want = replace(
                    live[-1][1], created_at=e.created_at, status_since=e.status_since
                )
                assert e == want
        engine.dispose()


def test_a_regroup_keeps_when_the_status_began(ledger_for: LedgerFor) -> None:
    # verifier #190: charged-after-cancel reads the cancel's time, not the regroup's
    led = ledger_for(1)
    m = merchant(led, "SYNTH STREAM")
    led.add_subscription(m, "subscription", "Streaming")
    led.cancel_subscription(m)
    cancelled = led.subscription(m)
    assert cancelled is not None and cancelled.status_since == cancelled.changed_at
    led.regroup_subscription(m, "subscription", "Software")
    after = led.subscription(m)
    assert after is not None and after.status == "user_cancelled"
    assert after.status_since == cancelled.changed_at < after.changed_at


def test_dismissing_a_never_listed_merchant_is_remembered(
    ledger_for: LedgerFor,
) -> None:
    # decided by orchestrator (#132): a no to a Merchant never listed still
    # writes a negative entry, so detection never suggests it again
    led = ledger_for(1)
    m = merchant(led, "SYNTH NEVER LISTED")

    led.not_a_subscription(m)

    (e,) = led.subscriptions()
    assert (e.merchant_id, e.status, e.source) == (m, "not_subscription", "user")
    assert not led.record_detection(
        m, "subscription", "Streaming", "pattern", "suggested", date.today()
    )


def test_deleting_a_merchant_removes_its_whole_registry_history(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH DELETED")
    other = merchant(led, "SYNTH KEPT")
    led.add_subscription(m, "subscription", "Streaming")
    led.not_a_subscription(m)
    led.add_subscription(other, "subscription", "Streaming")
    db = led._db  # pyright: ignore[reportPrivateUsage]

    db.execute(db.delete(schema.merchant).where(schema.merchant.c.id == m))

    rows = db.execute(
        db.select(schema.subscription_entry, schema.subscription_entry.c.merchant_id)
    ).all()
    assert [r.merchant_id for r in rows] == [other]


def test_confirming_a_user_added_entry_keeps_user_added(
    ledger_for: LedgerFor,
) -> None:
    # decided by orchestrator (#132): confirming changes status, not origin
    led = ledger_for(1)
    m = merchant(led, "SYNTH ADDED")
    led.add_subscription(m, "subscription", "Streaming")
    led.cancel_subscription(m)

    led.confirm_subscription(m)

    e = led.subscription(m)
    assert e is not None
    assert (e.status, e.reason) == ("active", "user_added")


def test_confirming_after_a_removal_is_user_confirmed(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    m = merchant(led, "SYNTH REMOVED")
    led.add_subscription(m, "subscription", "Streaming")
    led.not_a_subscription(m)

    led.confirm_subscription(m, "subscription", "Streaming")

    e = led.subscription(m)
    assert e is not None
    assert e.reason == "user_confirmed"
