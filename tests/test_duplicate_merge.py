# synthetic
"""#158 item 1: merging a confirmed Possible Duplicate (ADR 0014 note). The
dropped synced copy is an overlay row, never deleted: while it is live the row
counts in no total, and undo brings it back exactly."""

import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Engine

from finance_agent import api, categorise
from finance_agent.assistant import ToolCall
from finance_agent.assistant.tools import run
from finance_agent.ledger import InvalidMerge, Ledger, UnknownTarget
from finance_agent.ledger.review import Answer, ReviewQueue
from test_dedupe import temp_ledger

LONDON = ZoneInfo("Europe/London")
MONTH = "2026-01"
LedgerFor = Callable[[int], Ledger]

type Row = tuple[int, int, str]  # day of January, amount in minor units, text


def _ts(day: int, n: int) -> int:
    return int(datetime.combine(date(2026, 1, day), time(12), LONDON).timestamp()) + n


def synced(rows: list[Row], twins: int = 1, account: str = "ACC-1") -> bytes:
    """One SimpleFIN response; rows[0] is also sent `twins` more times under
    other IDs, as providers do (ADR 0014 (a))."""
    out: list[dict[str, Any]] = []
    for n, (day, minor, text) in enumerate(rows):
        out.append(
            {
                "id": f"{account}-{n}",
                "posted": _ts(day, n),
                "amount": str(Decimal(minor).scaleb(-2)),
                "description": text,
            }
        )
    out += [out[0] | {"id": f"{account}-twin{k}"} for k in range(twins)]
    return json.dumps(
        {
            "errors": [],
            "accounts": [
                {
                    "org": {"name": "Synthetic Bank"},
                    "id": account,
                    "name": "Synthetic Current",
                    "currency": "GBP",
                    "balance": "100.00",
                    "balance-date": _ts(28, 999),
                    "transactions": out,
                }
            ],
        }
    ).encode()


def figures(led: Ledger) -> tuple[Any, ...]:
    s = led.summarise_month(MONTH)
    b = led.category_breakdown(MONTH)
    return (
        s.money_in,
        s.money_out,
        s.net,
        b.spending_after_refunds,
        b.refunds,
        tuple((c.name, c.amount) for c in b.categories),
        len(led.transactions()),
        led.search_transactions(month=MONTH).total,
    )


def the_group(led: Ledger) -> list[int]:
    (group,) = led.possible_duplicates()
    return sorted(group)


ROWS = st.lists(
    st.tuples(
        st.integers(1, 28),
        st.integers(-50_000, 50_000).filter(bool),
        st.sampled_from(["SYNTH CAFE", "SYNTH GROCER", "SYNTH REFUND", "SYNTH PAY"]),
    ),
    min_size=1,
    max_size=8,
    unique_by=lambda r: (r[0], r[2]),  # only rows[0] has twins
)


@settings(max_examples=15, deadline=None)
@given(rows=ROWS, twins=st.integers(1, 3), merges=st.data())
def test_money_drops_by_exactly_the_duplicate_and_undo_restores_it(
    rows: list[Row], twins: int, merges: st.DataObject
) -> None:
    with temp_ledger() as (led, _):
        led.apply_sync(synced(rows, twins))
        categorise.run(led._db.conn, 1)  # pyright: ignore[reportPrivateUsage]
        before = figures(led)
        group = the_group(led)
        k = merges.draw(st.integers(1, twins), label="merges")
        made = [led.merge_duplicate(t) for t in group[1 : k + 1]]
        s = led.summarise_month(MONTH)
        amount = Decimal(rows[0][1]).scaleb(-2) * k
        assert s.net == before[2] - amount
        assert s.money_in == before[0] - max(amount, Decimal(0))
        assert s.money_out == before[1] + min(amount, Decimal(0))
        assert len(led.transactions()) == before[6] - k
        assert led.search_transactions(month=MONTH).total == before[7] - k
        for m in reversed(made):
            led.revert_merge(m)
        assert figures(led) == before


@settings(max_examples=10, deadline=None)
@given(rows=ROWS)
def test_merges_never_cross_users(rows: list[Row]) -> None:
    with temp_ledger() as (one, _):
        conn = one._db.conn  # pyright: ignore[reportPrivateUsage]
        two = Ledger(conn, 2)
        one.apply_sync(synced(rows))
        two.apply_sync(synced(rows, account="ACC-2"))
        theirs = figures(two)
        mine, other = the_group(one), the_group(two)
        for t in other:  # another user's row is unknown, never merged
            with pytest.raises(UnknownTarget):
                one.merge_duplicate(t)
        m = one.merge_duplicate(mine[1])
        with pytest.raises(UnknownTarget):
            two.revert_merge(m)
        assert figures(two) == theirs and two.merged_ids() == set()
        assert one.merged_ids() == {mine[1]}


def test_the_last_unmerged_row_csv_rows_and_outsiders_are_refused(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    led.apply_sync(synced([(5, -350, "SYNTH CAFE"), (6, -900, "SYNTH GROCER")], 2))
    a, b, c = the_group(led)
    led.merge_duplicate(b)
    with pytest.raises(InvalidMerge):
        led.merge_duplicate(b)  # already merged
    led.merge_duplicate(c)
    with pytest.raises(InvalidMerge):
        led.merge_duplicate(a)  # the group's last unmerged row
    lone = next(t.id for t in led.transactions() if t.id not in (a, b, c))
    with pytest.raises(InvalidMerge):
        led.merge_duplicate(lone)  # in no Possible Duplicate
    with pytest.raises(UnknownTarget):
        led.revert_merge(10_000)


def test_the_assistant_tools_agree_after_a_merge(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    led.apply_sync(synced([(5, -350, "SYNTH CAFE"), (6, -900, "SYNTH GROCER")]))
    categorise.run(led._db.conn, 1)  # pyright: ignore[reportPrivateUsage]
    led.merge_duplicate(the_group(led)[1])
    conn = led._db.conn  # pyright: ignore[reportPrivateUsage]
    out: Any = run(
        "ask", ToolCall(name="spending_summary", arguments={"month": MONTH}), conn
    )
    s = led.summarise_month(MONTH)
    assert out.money_out.minor == int(s.money_out.scaleb(2)) == 1250
    found: Any = run("correct", ToolCall(name="find_transactions", arguments={}), conn)
    assert found.total == 2


def test_the_review_queue_merges_and_undo_unmerges(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    led.apply_sync(synced([(5, -350, "SYNTH CAFE")], 2))
    conn = led._db.conn  # pyright: ignore[reportPrivateUsage]
    q = ReviewQueue(conn, 1)
    (dup,) = [i for i in q.items() if i.type == "possible_duplicate"]
    assert dup.choices == ("keep_both", "merge")
    _, b, c = dup.transaction_ids
    q.answer(dup.id, dup.version, Answer(choice="merge", transaction_id=c))
    # two rows still unmerged: the group stays queued, as a changed item
    (again,) = [i for i in q.items() if i.type == "possible_duplicate"]
    assert again.version != dup.version
    with pytest.raises(Exception, match="merge takes"):
        q.answer(again.id, again.version, Answer(choice="merge", transaction_id=-1))
    # no row named (what the web sends): the newest copy that may go, b
    q.answer(again.id, again.version, Answer(choice="merge"))
    assert [i for i in q.items() if i.type == "possible_duplicate"] == []
    assert led.merged_ids() == {b, c}
    assert q.undo() is None
    assert led.merged_ids() == {c}
    led.revert_merge(next(iter(_merges(led))))  # reverted elsewhere
    assert q.undo() is not None  # says so, changes nothing
    assert led.merged_ids() == set()


def _merges(led: Ledger) -> list[int]:
    from finance_agent.storage import schema

    m = schema.duplicate_merge
    return list(
        led._db.execute(  # pyright: ignore[reportPrivateUsage]
            led._db.select(m, m.c.id).where(m.c.reverted_at.is_(None))  # pyright: ignore[reportPrivateUsage]
        ).scalars()
    )


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_api_merge_is_scoped_to_the_current_user(
    engine: Engine, client: TestClient
) -> None:
    with engine.begin() as conn:
        Ledger(conn, 1).apply_sync(synced([(5, -350, "SYNTH CAFE")]))
        Ledger(conn, 2).apply_sync(synced([(5, -350, "SYNTH CAFE")], account="X"))
        theirs = sorted(next(iter(Ledger(conn, 2).possible_duplicates())))
    (dup,) = [
        i
        for i in client.get("/api/review-queue").json()["items"]
        if i["type"] == "possible_duplicate"
    ]
    assert not set(dup["transaction_ids"]) & set(theirs)
    body = {"version": dup["version"], "choice": "merge"}
    url = f"/api/review-queue/{dup['id']}/answer"
    r = client.post(url, json=body | {"transaction_id": theirs[1]})
    assert r.status_code == 422  # not one of this item's rows
    r = client.post(url, json=body | {"transaction_id": dup["transaction_ids"][1]})
    assert r.status_code == 200
    assert all(i["type"] != "possible_duplicate" for i in r.json()["items"])
    s = client.get(f"/api/summary?month={MONTH}").json()
    assert s["money_out"]["minor"] == 350
    with engine.begin() as conn:
        assert Ledger(conn, 2).merged_ids() == set()
        assert Ledger(conn, 2).summarise_month(MONTH).money_out == Decimal("7.00")


def _two_groups(a: Row, b: Row, twins_a: int, twins_b: int) -> bytes:
    """One response whose rows `a` and `b` each arrive 1 + twins times."""
    body = json.loads(synced([a, b], twins_a))
    txns = body["accounts"][0]["transactions"]
    txns += [txns[1] | {"id": f"ACC-1-btwin{k}"} for k in range(twins_b)]
    return json.dumps(body).encode()


@settings(max_examples=25, deadline=None)
@given(
    a=st.integers(-50_000, 50_000).filter(bool),
    b=st.integers(-50_000, 50_000).filter(bool),
    twins=st.tuples(st.integers(1, 3), st.integers(1, 3)),
    steps=st.lists(st.tuples(st.booleans(), st.integers(0, 7)), max_size=14),
)
def test_random_merge_and_undo_sequences_match_a_reference(
    a: int, b: int, twins: tuple[int, int], steps: list[tuple[bool, int]]
) -> None:
    """Verifier (#224): interleaved merges and undos over two groups. After
    every step the totals equal a reference summed from the raw rows less the
    merged ones, a refusal happens exactly when ADR 0014 says, and no group is
    ever left with every row merged."""
    with temp_ledger() as (led, _):
        led.apply_sync(
            _two_groups((5, a, "SYNTH CAFE"), (9, b, "SYNTH GROCER"), *twins)
        )
        groups = [frozenset(g) for g in led.possible_duplicates()]
        assert len(groups) == 2
        ids = sorted(groups[0] | groups[1])
        amount = {t.id: t.amount for t in led.transactions(merged=True)}
        live: dict[int, int] = {}  # transaction id -> merge id

        for is_merge, pick in steps:
            if is_merge:
                t = ids[pick % len(ids)]
                g = next(x for x in groups if t in x)
                if t not in live and len(g - live.keys()) > 1:
                    live[t] = led.merge_duplicate(t)
                else:
                    with pytest.raises(InvalidMerge):
                        led.merge_duplicate(t)
            elif live:
                t = sorted(live)[pick % len(live)]
                m = live.pop(t)
                led.revert_merge(m)
                with pytest.raises(UnknownTarget):
                    led.revert_merge(m)  # an undo never applies twice

            assert led.merged_ids() == set(live)
            assert all(g - live.keys() for g in groups)
            kept = [v for i, v in amount.items() if i not in live]
            s = led.summarise_month(MONTH)
            assert s.net == sum(kept, Decimal(0))
            assert s.money_in == sum((v for v in kept if v > 0), Decimal(0))
            assert s.money_out == -sum((v for v in kept if v < 0), Decimal(0))
            assert len(led.transactions()) == len(kept)
            assert led.search_transactions(month=MONTH).total == len(kept)
