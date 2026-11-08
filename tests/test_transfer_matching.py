# synthetic
"""#215 C: own-account Transfer matching over synced rows. An amount and its
exact opposite in two of the user's Accounts within TRANSFER_DAYS pair once;
merged Possible Duplicates never pair, and a user's "not a Transfer" wins."""

import contextlib
import json
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from finance_agent.ledger import (
    TRANSFER_DAYS,
    InvalidMerge,
    Ledger,
    Transaction,
    UnknownTarget,
)
from finance_agent.storage import schema
from test_dedupe import temp_ledger

LONDON = ZoneInfo("Europe/London")
ACCOUNTS = ("ACC-A", "ACC-B", "ACC-C")

type Row = tuple[int, int, int]  # account index, day of January, minor units


def payload(rows: list[Row], prefix: str = "") -> bytes:
    """One SimpleFIN response; each row gets a unique description so no two
    are Possible Duplicates of each other."""
    accounts: dict[int, list[dict[str, Any]]] = {i: [] for i in range(len(ACCOUNTS))}
    for n, (acc, day, minor) in enumerate(rows):
        ts = datetime.combine(date(2026, 1, day), time(12), LONDON).timestamp()
        accounts[acc].append(
            {
                "id": f"{prefix}T{n}",
                "posted": int(ts) + n,
                "amount": str(Decimal(minor).scaleb(-2)),
                "description": f"SYNTH MOVE {n}",
            }
        )
    return json.dumps(
        {
            "errors": [],
            "accounts": [
                {
                    "org": {"name": "Synthetic Bank"},
                    "id": prefix + ACCOUNTS[i],
                    "name": "Synthetic Current",
                    "currency": "GBP",
                    "balance": "100.00",
                    "balance-date": int(
                        datetime.combine(date(2026, 1, 31), time(12)).timestamp()
                    ),
                    "transactions": txns,
                }
                for i, txns in accounts.items()
            ],
        }
    ).encode()


ROWS = st.lists(
    st.tuples(
        st.integers(0, len(ACCOUNTS) - 1),
        st.integers(1, 28),
        st.sampled_from([-1250, 1250, -500, 500, -9999, 9999, -1, 1]),
    ),
    max_size=14,
)


def links(led: Ledger) -> set[tuple[int, int]]:
    tl = schema.transfer_link
    db = led._db  # pyright: ignore[reportPrivateUsage]
    return {
        (r.from_transaction_id, r.to_transaction_id)
        for r in db.execute(
            db.select(tl, tl.c.from_transaction_id, tl.c.to_transaction_id)
        )
    }


def check_pairs(led: Ledger) -> dict[int, Transaction]:
    by_id = {t.id: t for t in led.transactions(merged=True)}
    pairs = links(led)
    legs = Counter(leg for p in pairs for leg in p)
    assert all(n == 1 for n in legs.values())  # no row is in two pairs
    for out_id, in_id in pairs:
        o, i = by_id[out_id], by_id[in_id]  # both this user's rows
        assert o.amount < 0 and o.amount == -i.amount and o.currency == i.currency
        assert o.account_id != i.account_id  # never inside one Account
        assert abs((o.posted_on - i.posted_on).days) <= TRANSFER_DAYS
        assert o.transfer_with == i.id and i.transfer_with == o.id  # evidence
    return by_id


def expected(led: Ledger, rows: list[Row]) -> tuple[Decimal, Decimal]:
    """Money in and out of the raw rows, less the paired legs."""
    legs = {leg for p in links(led) for leg in p}
    amounts = [t.amount for t in led.transactions() if t.id not in legs]
    assert len(led.transactions()) == len(rows)
    return sum((a for a in amounts if a > 0), Decimal(0)), -sum(
        (a for a in amounts if a < 0), Decimal(0)
    )


@settings(max_examples=40, deadline=None)
@given(rows=ROWS)
def test_totals_change_only_by_matched_pairs_and_reruns_are_idempotent(
    rows: list[Row],
) -> None:
    with temp_ledger() as (led, _):
        led.apply_sync(payload(rows))
        check_pairs(led)
        s = led.summarise_month("2026-01")
        money_in, money_out = expected(led, rows)
        assert (s.money_in, s.money_out) == (money_in, money_out)
        assert s.net == sum((Decimal(m).scaleb(-2) for *_, m in rows), Decimal(0))
        assert s.transfers_in == s.transfers_out  # each pair nets to zero
        before = links(led)
        led.apply_sync(payload(rows) + b" ")  # the same rows, a new sync
        assert links(led) == before


@settings(max_examples=20, deadline=None)
@given(rows=ROWS)
def test_matching_is_deterministic(rows: list[Row]) -> None:
    got: list[set[tuple[int, int]]] = []
    for _ in range(2):
        with temp_ledger() as (led, _):
            led.apply_sync(payload(rows))
            got.append(links(led))
    assert got[0] == got[1]


@settings(max_examples=20, deadline=None)
@given(rows=ROWS)
def test_never_across_users(rows: list[Row]) -> None:
    with temp_ledger() as (one, _):
        two = Ledger(one._db.conn, 2)  # pyright: ignore[reportPrivateUsage]
        # user 2 holds the exact opposite of every row of user 1, same days
        two.apply_sync(payload([(a, d, -m) for a, d, m in rows], prefix="U2"))
        one.apply_sync(payload(rows))
        mine = {t.id for t in one.transactions()}
        theirs = {t.id for t in two.transactions()}
        assert {leg for p in links(one) for leg in p} <= mine
        assert {leg for p in links(two) for leg in p} <= theirs
        check_pairs(one)
        check_pairs(two)


@settings(max_examples=30, deadline=None)
@given(rows=ROWS, data=st.data())
def test_a_rejected_row_is_never_paired_again_until_undone(
    rows: list[Row], data: st.DataObject
) -> None:
    with temp_ledger() as (led, _):
        led.apply_sync(payload(rows))
        ids = sorted(t.id for t in led.transactions())
        picked = data.draw(
            st.lists(st.sampled_from(ids), unique=True) if ids else st.just([])
        )
        made = [led.not_a_transfer(i) for i in picked]
        led.apply_sync(payload(rows) + b" ")  # a rerun never overrides the user
        by_id = check_pairs(led)
        assert all(by_id[i].transfer_with is None for i in picked)
        s = led.summarise_month("2026-01")
        assert (s.money_in, s.money_out) == expected(led, rows)
        for m in made:
            led.revert_not_a_transfer(m)
        check_pairs(led)
        after = links(led)
        with temp_ledger() as (fresh, _):  # undoing every rejection restores
            fresh.apply_sync(payload(rows))  # the pairing of untouched rows
            assert links(fresh) == after
        led.apply_sync(payload(rows) + b"  ")
        assert links(led) == after


def test_rejecting_a_leg_unpairs_both_and_frees_the_other_leg(
    ledger_for: Any,
) -> None:
    led: Ledger = ledger_for(1)
    # A out, B in (same day), C in a day later: A pairs with B first
    led.apply_sync(payload([(0, 5, -1250), (1, 5, 1250), (2, 6, 1250)]))
    by_desc = {t.description: t for t in led.transactions()}
    a, b, c = (by_desc[f"SYNTH MOVE {n}"] for n in range(3))
    assert by_desc["SYNTH MOVE 0"].transfer_with == b.id
    rejected = led.not_a_transfer(b.id)
    by_id = {t.id: t for t in led.transactions()}
    assert by_id[b.id].transfer_with is None
    assert by_id[a.id].transfer_with == c.id  # A's next-best leg
    with pytest.raises(UnknownTarget):
        led.revert_not_a_transfer(rejected + 1000)
    led.revert_not_a_transfer(rejected)
    with pytest.raises(UnknownTarget):
        led.revert_not_a_transfer(rejected)  # already undone
    by_id = {t.id: t for t in led.transactions()}
    assert by_id[a.id].transfer_with == b.id  # the undo restores A and B
    assert by_id[c.id].transfer_with is None


def test_another_users_row_cannot_be_rejected(ledger_for: Any) -> None:
    one: Ledger = ledger_for(1)
    two: Ledger = ledger_for(2)
    two.apply_sync(payload([(0, 5, -1250), (1, 5, 1250)], prefix="U2"))
    theirs = [t.id for t in two.transactions()]
    for t in theirs:
        with pytest.raises(UnknownTarget):
            one.not_a_transfer(t)
    made = two.not_a_transfer(theirs[0])
    with pytest.raises(UnknownTarget):
        one.revert_not_a_transfer(made)


def test_a_merged_duplicate_is_never_paired_and_its_live_twin_is(
    ledger_for: Any,
) -> None:
    """#224 verifier note: a merged synced copy must not hold the pair."""
    led: Ledger = ledger_for(1)
    rows = [(0, 5, -1250), (1, 5, 1250)]
    led.apply_sync(payload(rows))
    body = json.loads(payload(rows))
    body["accounts"][0]["transactions"][0]["id"] = "T0-twin"  # A re-issued
    led.apply_sync(json.dumps(body).encode())  # ADR 0014 (b)
    (group,) = led.possible_duplicates()
    leg = next(t for t in led.transactions() if t.amount > 0)
    paired = leg.transfer_with
    assert paired in group
    m = led.merge_duplicate(paired)  # relinks at once, before any sync
    by_id = check_pairs(led)
    (twin,) = group - {paired}
    assert by_id[paired].transfer_with is None
    assert by_id[twin].transfer_with == leg.id
    assert all(paired not in p for p in links(led))
    s = led.summarise_month("2026-01")
    assert s.transfers_in == s.transfers_out == Decimal("12.50")
    assert (s.money_in, s.money_out) == (0, 0)  # nothing counted as Income
    led.revert_merge(m)  # the twin is back; the pair stays a pair
    check_pairs(led)
    assert {leg for p in links(led) for leg in p} & set(group)


def test_a_rejection_moves_to_the_row_that_survives_a_csv_synced_merge(
    ledger_for: Any,
) -> None:
    """ADR 0014: a bank-file row and its synced copy become one Transaction;
    the user's "not a Transfer" on the copy stays with the survivor."""
    led: Ledger = ledger_for(1)
    led.apply_sync(payload([(0, 5, -1250), (1, 5, 1250), (1, 6, 1250)]))
    by_desc = {t.description: t for t in led.transactions()}
    a, b, c = (by_desc[f"SYNTH MOVE {n}"].id for n in range(3))
    made = led.not_a_transfer(b)
    assert led.transaction(a).transfer_with == c  # pyright: ignore[reportOptionalMemberAccess]
    # c stands in for the bank-file row; b is its synced copy
    led._merge(c, b)  # pyright: ignore[reportPrivateUsage]
    led._link_transfers()  # pyright: ignore[reportPrivateUsage]
    r = schema.transfer_rejection
    db = led._db  # pyright: ignore[reportPrivateUsage]
    rows = db.execute(db.select(r, r.c.id, r.c.transaction_id)).all()
    assert [(x.id, x.transaction_id) for x in rows] == [(made, c)]
    assert links(led) == set()  # the survivor carries the user's word
    led.revert_not_a_transfer(made)
    assert links(led) == {(a, c)}
    (row,) = db.execute(db.select(r, r.c.reverted_at)).all()
    assert row.reverted_at is not None  # history kept


# few amounts and close days, so legs compete and twins hold pairs
CROWDED = st.lists(
    st.tuples(
        st.integers(0, len(ACCOUNTS) - 1),
        st.integers(5, 8),
        st.sampled_from([-1250, 1250]),
    ),
    min_size=2,
    max_size=8,
)


@settings(max_examples=60, deadline=None)
@given(rows=CROWDED, twins=st.data())
def test_any_sequence_of_merges_rejections_and_undos_matches_a_fresh_pairing(
    rows: list[Row], twins: st.DataObject
) -> None:
    """Adversarial (#215 C verifier): after any history of merge, unmerge,
    reject, revert and resync, the pairs equal those of a fresh ledger given
    only the end state, so money out never depends on the order of events."""
    doubled = twins.draw(
        st.lists(st.booleans(), min_size=len(rows), max_size=len(rows))
    )
    body = json.loads(payload(rows))
    for acc in body["accounts"]:
        for t in list(acc["transactions"]):
            if doubled[int(t["id"][1:])]:
                acc["transactions"].append(t | {"id": t["id"] + "-twin"})
    raw = json.dumps(body).encode()
    with temp_ledger() as (led, _):
        led.apply_sync(raw)
        ids = sorted(t.id for t in led.transactions(merged=True))
        if not ids:
            return
        merges: dict[int, int] = {}
        rejections: dict[int, int] = {}
        ops = twins.draw(
            st.lists(
                st.tuples(
                    st.sampled_from(["merge", "unmerge", "reject", "revert", "sync"]),
                    st.sampled_from(ids),
                ),
                max_size=12,
            )
        )
        for n, (op, tid) in enumerate(ops):
            if op == "merge" and tid not in merges:
                with contextlib.suppress(InvalidMerge):  # not a mergeable copy
                    merges[tid] = led.merge_duplicate(tid)
            elif op == "unmerge" and tid in merges:
                led.revert_merge(merges.pop(tid))
            elif op == "reject" and tid not in rejections:
                rejections[tid] = led.not_a_transfer(tid)
            elif op == "revert" and tid in rejections:
                led.revert_not_a_transfer(rejections.pop(tid))
            elif op == "sync":
                led.apply_sync(raw + b" " * (n + 1))
            by_id = check_pairs(led)
            legs = {leg for p in links(led) for leg in p}
            assert not legs & (set(merges) | set(rejections))
            assert all(i in by_id for i in legs)
            s = led.summarise_month("2026-01")
            live = [t.amount for t in led.transactions() if t.id not in legs]
            assert s.money_out == -sum((a for a in live if a < 0), Decimal(0))
            assert s.money_in == sum((a for a in live if a > 0), Decimal(0))
        with temp_ledger() as (fresh, _):
            fresh.apply_sync(raw)
            for tid in merges:
                fresh.merge_duplicate(tid)
            for tid in rejections:
                fresh.not_a_transfer(tid)
            assert links(fresh) == links(led)
