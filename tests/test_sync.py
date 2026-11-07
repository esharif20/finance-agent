"""#27: synced Transactions through the Ledger interface (ADR 0014)."""

import json
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from finance_agent.ledger import Ledger
from synthetic_lloyds import History, generate_histories, render_export
from synthetic_lloyds.generator import SORT_CODE, _with_balances  # pyright: ignore[reportPrivateUsage]
from synthetic_simplefin import LONDON, provider_id, response, sync_row
from test_dedupe import temp_ledger

LedgerFor = Callable[[int], Ledger]
WriteExport = Callable[[str], Path]

H = generate_histories(7, 40)[0]


def snapshot(led: Ledger) -> list[tuple[Any, ...]]:
    return [(t.posted_on, t.description, t.amount) for t in led.transactions()]


def same_day_repeats(led: Ledger) -> list[frozenset[int]]:
    """The history's deliberate same-day repeats (ADR 0006): ADR 0014 (a) groups."""
    groups: dict[tuple[Any, ...], set[int]] = {}
    for t in led.transactions():
        groups.setdefault((t.posted_on, t.amount, t.description), set()).add(t.id)
    return [frozenset(g) for g in groups.values() if len(g) > 1]


def edit(raw: bytes, **fields: Any) -> bytes:
    """Change the first row of the first account."""
    doc = json.loads(raw)
    doc["accounts"][0]["transactions"][0].update(fields)
    return json.dumps(doc).encode()


def test_posted_rows_enter_the_ledger_and_pending_rows_do_not(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    r = led.apply_sync(response(H, 0, 10, pending=2))
    assert (r.added, r.already_known) == (10, 0)
    assert len(r.accounts) == 1
    assert len(led.transactions()) == 10
    jan = led.summarise_month("2026-01")
    expected = sum(t.amount_minor for t in H.txns[:10])
    assert jan.net == Decimal(expected).scaleb(-2)


def test_a_stored_sync_bumps_the_ledger_version_and_a_repeat_does_not(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    raw = response(H, 0, 5)
    version = led.ledger_version()
    led.apply_sync(raw)
    assert led.ledger_version() == version + 1
    led.apply_sync(raw)  # same bytes: nothing stored
    assert led.ledger_version() == version + 1


def test_a_known_id_never_changes_and_is_reported(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    raw = response(H, 0, 5)
    led.apply_sync(raw)
    before = snapshot(led)
    r = led.apply_sync(edit(raw, amount="-999.99", description="CHANGED"))
    assert (r.added, r.already_known, r.changed_known) == (0, 5, 1)
    assert snapshot(led) == before


def test_one_id_with_differing_rows_rejects_that_accounts_sync(
    ledger_for: LedgerFor,
) -> None:
    doc = json.loads(response(H, 0, 3))
    rows = doc["accounts"][0]["transactions"]
    rows.append(rows[0] | {"amount": "-1.23"})
    r = ledger_for(1).apply_sync(json.dumps(doc).encode())
    assert (r.added, r.rejected_accounts) == (0, 1)


def test_same_purchase_under_distinct_ids_is_kept_and_flagged(
    ledger_for: LedgerFor,
) -> None:
    doc = json.loads(response(H, 0, 1))
    row = doc["accounts"][0]["transactions"][0]
    doc["accounts"][0]["transactions"] += [row | {"id": "7"}, row | {"id": "007"}]
    led = ledger_for(1)
    r = led.apply_sync(json.dumps(doc).encode())
    assert (r.added, r.provider_duplicates) == (3, 3)
    # ADR 0014 (a): surfaced as one Possible Duplicate, and it persists
    assert led.possible_duplicates() == [frozenset(t.id for t in led.transactions())]
    led.apply_sync(json.dumps(doc).encode() + b" ")  # same rows, other bytes
    assert len(led.possible_duplicates()) == 1


def test_first_sync_anchors_and_a_missing_row_opens_a_gap(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    assert led.apply_sync(response(H, 0, 10)).gaps == []
    lost = H.txns[14]
    assert lost.amount_minor != 0
    r = led.apply_sync(response(H, 8, 20, drop={14}))
    assert [g.amount for g in r.gaps] == [Decimal(lost.amount_minor).scaleb(-2)]
    assert led.sync_gaps() == r.gaps


@settings(max_examples=25, deadline=None)
@given(st.integers(0, 3), st.integers(0, 3), st.integers(-500, 500), st.booleans())
def test_pending_rows_never_open_a_gap_but_a_real_gap_still_does(
    first_pending: int, later_pending: int, offset: int, lose_row: bool
) -> None:
    # Spike #26: balance == available-balance with a pending row present, so
    # balance includes pending rows and ADR 0014 reduces B by their total.
    lost = H.txns[14].amount_minor if lose_row else 0
    with temp_ledger() as (led, _):
        led.apply_sync(response(H, 0, 10, pending=first_pending))
        r = led.apply_sync(
            response(
                H,
                10,
                20,
                pending=later_pending,
                balance_offset=offset,
                drop={14} if lose_row else (),
            )
        )
        expected = offset + lost
        assert [g.amount for g in r.gaps] == (
            [Decimal(expected).scaleb(-2)] if expected else []
        )


def test_a_repeated_pending_id_cannot_cancel_a_real_gap() -> None:
    # Adversarial: the feed lists one pending row twice under one ID and drops
    # a posted row of the same amount. Subtracting the pending row twice would
    # net the missing row's Gap to zero; one ID is one row (ADR 0014).
    with temp_ledger() as (led, _):
        led.apply_sync(response(H, 0, 10))
        doc = json.loads(response(H, 10, 20, pending=1))
        rows = doc["accounts"][0]["transactions"]
        lost = next(i for i in range(10, 19) if H.txns[i].amount_minor < 0)
        amount = Decimal(H.txns[lost].amount_minor).scaleb(-2)
        rows[:] = [r for r in rows if r["id"] != f"{lost:05d}"]
        pend = next(r for r in rows if r.get("pending"))
        pend["amount"] = str(amount)
        rows.append(dict(pend))
        acc = doc["accounts"][0]
        acc["balance"] = str(Decimal(acc["balance"]) + Decimal(1) + amount)
        r = led.apply_sync(json.dumps(doc).encode())
        assert [g.amount for g in r.gaps] == [amount]


def test_identical_purchases_across_the_coverage_boundary_are_never_merged() -> None:
    # Three real coffees on 1, 3 and 5 Jan. The CSV holds the first two, the sync
    # the last two. Sorted pairing gives (1, 3) and (3, 5), both within 3 days, so
    # three purchases become two, with no Possible Duplicate and no Gap. The
    # amended rule (#74) keeps all four rows and surfaces them: the 1 Jan row's
    # counterpart could predate the sync, so the count is not determined.
    ev = [(date(2026, 1, d), "DEB", "CAFE NERO 0042", -350) for d in (1, 3, 5)]
    h = History(SORT_CODE, "00000001", _with_balances(ev, 100000))
    with temp_ledger() as (led, d):
        csv = d / "export.csv"
        csv.write_text(render_export(h, 0, 2).text)
        led.import_files([csv])
        led.apply_sync(response(h, 1, 3))
        (acc,) = [a for a in led.accounts() if a.masked_number == "••••0001"]
        led.link_synced_account("ACC-1", acc.id)
        assert len(led.transactions()) == 4
        assert [len(g) for g in led.possible_duplicates()] == [4]


def test_csv_history_before_the_anchor_never_opens_a_gap(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    led = ledger_for(1)
    led.import_files([write_export(render_export(H, 0, 30).text)])
    (csv_account,) = led.accounts()
    led.link_synced_account("ACC-1", csv_account.id)
    led.apply_sync(response(H, 20, 30))
    r = led.apply_sync(response(H, 25, 40))
    assert r.gaps == [] and led.ambiguous_matches() == []
    assert len(led.transactions()) == 40


def test_ambiguous_rows_stay_separate_and_are_possible_duplicates(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    led = ledger_for(1)
    led.import_files([write_export(render_export(H, 0, 40).text)])
    (csv_account,) = led.accounts()
    led.link_synced_account("ACC-1", csv_account.id)
    doc = json.loads(response(H, 15, 25))  # well inside the CSV's history
    row = doc["accounts"][0]["transactions"][0]
    doc["accounts"][0]["transactions"].append(row | {"id": "again"})
    led.apply_sync(json.dumps(doc).encode())
    assert len(led.transactions()) == 42  # 9 pairs merged; 1 CSV + 2 synced kept
    (group,) = led.ambiguous_matches()
    assert len(group) == 3
    assert group in led.possible_duplicates()


# --- properties ---------------------------------------------------------------


@st.composite
def histories_and_split(draw: st.DrawFn) -> tuple[History, int, int, int]:
    h = generate_histories(draw(st.integers(0, 2**32 - 1)), draw(st.integers(5, 40)))[0]
    n = len(h.txns)
    a = draw(st.integers(1, n))
    b = draw(st.integers(0, a - 1))
    return h, a, b, n


@settings(max_examples=25, deadline=None)
@given(histories_and_split())
def test_syncing_twice_changes_nothing(case: tuple[History, int, int, int]) -> None:
    h, a, b, n = case
    with temp_ledger() as (led, _):
        led.apply_sync(response(h, 0, a))
        led.apply_sync(response(h, b, n))
        before = (snapshot(led), led.sync_gaps())
        again = led.apply_sync(response(h, b, n))
        assert again.added == 0
        led.apply_sync(response(h, 0, n))  # different bytes, same rows
        assert (snapshot(led), led.sync_gaps()) == before
        assert len(before[0]) == n and before[1] == []


# CSV plus sync never double-counting is tested through the CLI:
# test_cli_sync.py::test_csv_and_sync_through_the_cli_never_double_count


@settings(max_examples=25, deadline=None)
@given(histories_and_split(), st.data())
def test_a_feed_gap_opens_a_reconciliation_gap(
    case: tuple[History, int, int, int], data: st.DataObject
) -> None:
    h, a, _, n = case
    assume(a < n)
    lost = data.draw(st.integers(a, n - 1))
    assume(h.txns[lost].amount_minor != 0)
    with temp_ledger() as (led, _):
        led.apply_sync(response(h, 0, a))
        r = led.apply_sync(response(h, a, n, drop={lost}))
        assert [g.amount for g in r.gaps] == [
            Decimal(h.txns[lost].amount_minor).scaleb(-2)
        ]


def test_a_reissued_id_is_a_possible_duplicate_and_opens_a_gap(
    ledger_for: LedgerFor,
) -> None:
    """ADR 0014 (b): a known row vanishes from a later response covering its date
    while a new ID with the same amount and text appears within 3 days."""
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 10))
    doc = json.loads(response(H, 0, 12))
    rows = doc["accounts"][0]["transactions"]
    rows[9] = rows[9] | {"id": "reissued", "posted": rows[9]["posted"] + 86400}
    r = led.apply_sync(json.dumps(doc).encode())
    amount = Decimal(H.txns[9].amount_minor).scaleb(-2)
    twice = frozenset(t.id for t in led.transactions() if t.amount == amount)
    assert len(twice) == 2
    assert twice in led.possible_duplicates()
    # counted twice, so the Gap is the row's negation (ADR 0014)
    assert [g.amount for g in r.gaps] == [-amount]


def test_a_row_aged_out_of_the_feed_is_never_a_duplicate_or_deleted(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 20))
    r = led.apply_sync(response(H, 10, 30))  # rows 0-9 retained no longer
    assert len(led.transactions()) == 30 and r.gaps == []
    assert set(led.possible_duplicates()) == set(same_day_repeats(led))


def test_a_pending_row_posting_under_a_new_id_counts_once(
    ledger_for: LedgerFor,
) -> None:
    led = ledger_for(1)
    doc = json.loads(response(H, 0, 10))
    rows = doc["accounts"][0]["transactions"]
    posted = rows.pop()
    rows.append(posted | {"id": "pending-id", "posted": 0, "pending": True})
    # the balance already includes the pending row (spike #26)
    doc["accounts"][0]["balance-date"] -= 60  # read before the row posted
    led.apply_sync(json.dumps(doc).encode())
    assert len(led.transactions()) == 9
    r = led.apply_sync(response(H, 0, 10))
    assert (r.added, r.gaps) == (1, [])
    assert len(led.transactions()) == 10
    assert set(led.possible_duplicates()) == set(same_day_repeats(led))


Step = tuple[str, int, int]  # ("csv", first, end) rows, or ("sync", from, to) days


@st.composite
def purchases_and_coverage(draw: st.DrawFn) -> tuple[History, list[int], list[Step]]:
    """#74: real purchases with a CSV date and a synced date up to 3 days apart,
    two texts and amounts so twins abound. A CSV export holds a window of the
    CSV history; a sync holds every purchase posted in its date range. Any
    number of each, in any order."""
    n = draw(st.integers(1, 10))
    days = sorted(draw(st.lists(st.integers(3, 17), min_size=n, max_size=n)))
    ev = [
        (
            date(2026, 1, 1) + timedelta(d),
            "DEB",
            draw(st.sampled_from(["CAFE NERO 0042", "TFL TRAVEL"])),
            draw(st.sampled_from([-350, -350, -900])),
        )
        for d in days
    ]
    skew = draw(st.lists(st.integers(-3, 3), min_size=n, max_size=n))
    steps: list[Step] = []
    for _ in range(draw(st.integers(1, 2))):
        a = draw(st.integers(0, n - 1))
        steps.append(("csv", a, draw(st.integers(a + 1, n))))
    for _ in range(draw(st.integers(1, 2))):
        lo, hi = sorted(draw(st.integers(0, 21)) for _ in range(2))
        steps.append(("sync", lo, hi))
    h = History(SORT_CODE, "00000001", _with_balances(ev, 100000))
    return h, skew, draw(st.permutations(steps))


def skewed_sync(h: History, skew: list[int], lo: int, hi: int, nonce: int = 0) -> bytes:
    start = date(2026, 1, 1)
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(h.txns):
        on = t.date + timedelta(skew[i])
        if lo <= (on - start).days <= hi:
            ts = int(datetime.combine(on, time(12), LONDON).timestamp()) + i
            rows.append(sync_row(t, provider_id(i), ts))
    end = datetime.combine(start + timedelta(hi), time(23), LONDON)
    acc = {
        "org": {"name": "Synthetic Bank"},
        "id": "ACC-1",
        "name": "Synthetic Current",
        "currency": "GBP",
        "balance": "0.00",
        "balance-date": int(end.timestamp()) + nonce,  # nonce: new bytes, same rows
        "transactions": rows,
    }
    return json.dumps({"errors": [], "accounts": [acc]}).encode()


@settings(max_examples=60, deadline=None)
@given(purchases_and_coverage(), st.booleans())
def test_no_purchase_is_lost_and_every_extra_row_is_surfaced(
    case: tuple[History, list[int], list[Step]], link_first: bool
) -> None:
    h, skew, steps = case
    present: set[int] = set()
    with temp_ledger() as (led, d):
        linked = False

        def link() -> None:
            (acc,) = [a for a in led.accounts() if a.masked_number == "••••0001"]
            led.link_synced_account("ACC-1", acc.id)

        def apply(step: Step, again: bool = False) -> None:
            kind, a, b = step
            if kind == "csv":
                # again: a sub-window, so new bytes for rows already held
                end = b - 1 if again and b - a > 1 else b
                path = d / f"export-{a}-{end}-{again}.csv"
                path.write_text(render_export(h, a, end).text)
                led.import_files([path])
            else:
                led.apply_sync(skewed_sync(h, skew, a, b, nonce=int(again)))

        for step in steps:
            apply(step)
            kind, a, b = step
            if kind == "csv":
                present |= set(range(a, b))
                if link_first and not linked:
                    link()
                    linked = True
            else:
                start = date(2026, 1, 1)
                present |= {
                    i
                    for i, t in enumerate(h.txns)
                    if a <= (t.date + timedelta(skew[i]) - start).days <= b
                }
        if not linked:
            link()
        txns, surfaced = snapshot(led), sorted(led.possible_duplicates(), key=sorted)
        extra = len(txns) - len(present)
        # no purchase is lost; an extra row only ever sits in a surfaced group
        assert 0 <= extra <= sum(len(g) for g in surfaced) // 2
        if not surfaced:
            assert extra == 0
            assert sum(t[2] for t in txns) == Decimal(
                sum(h.txns[i].amount_minor for i in present)
            ).scaleb(-2)
        for step in steps:  # re-importing and re-syncing change nothing
            apply(step, again=True)
        assert snapshot(led) == txns
        assert sorted(led.possible_duplicates(), key=sorted) == surfaced


def test_sync_since_follows_the_stalest_account(ledger_for: LedgerFor) -> None:
    led = ledger_for(1)
    led.apply_sync(response(H, 0, 5, account_id="STALE"))
    led.apply_sync(response(H, 0, 40, account_id="FRESH"))
    stale = H.txns[4].date
    assert led.sync_since(H.txns[39].date) <= stale


# --- verifier properties ------------------------------------------------------


@st.composite
def overlapping_windows(draw: st.DrawFn) -> tuple[History, list[tuple[int, int]]]:
    h = generate_histories(draw(st.integers(0, 2**32 - 1)), draw(st.integers(5, 40)))[0]
    n = len(h.txns)
    cuts = sorted(draw(st.lists(st.integers(0, n), min_size=1, max_size=4)))
    spans = [
        (max(0, a - draw(st.integers(0, 5))), b)
        for a, b in zip([0, *cuts], [*cuts, n], strict=False)
        if b > a
    ]
    return h, draw(st.permutations(spans)) if draw(st.booleans()) else spans


@settings(max_examples=25, deadline=None)
@given(overlapping_windows())
def test_overlapping_sync_windows_never_duplicate(
    case: tuple[History, list[tuple[int, int]]],
) -> None:
    h, spans = case
    with temp_ledger() as (led, _):
        for a, b in spans:
            led.apply_sync(response(h, a, b))
        txns = led.transactions()
        assert len(txns) == len(h.txns)
        assert sum(t.amount for t in txns) == Decimal(
            sum(t.amount_minor for t in h.txns)
        ).scaleb(-2)


@settings(max_examples=25, deadline=None)
@given(histories_and_split(), st.data())
def test_rows_missing_from_a_later_feed_are_never_deleted(
    case: tuple[History, int, int, int], data: st.DataObject
) -> None:
    h, _, _, n = case
    gone = data.draw(st.sets(st.integers(0, n - 1)))
    assume(n - 1 not in gone)  # the balance is read from the last row
    with temp_ledger() as (led, _):
        led.apply_sync(response(h, 0, n))
        before = snapshot(led)
        led.apply_sync(response(h, 0, n, drop=gone))
        assert snapshot(led) == before


def test_another_users_sync_never_reaches_this_users_views(
    ledger_for: LedgerFor,
) -> None:
    # Adversarial: two users sync the same provider IDs on one database; the
    # synced-key and gap joins must keep each user's rows apart (Scoped.on).
    doc = json.loads(response(H, 0, 1))
    row = doc["accounts"][0]["transactions"][0]
    doc["accounts"][0]["transactions"] += [row | {"id": "7"}]
    a, b = ledger_for(1), ledger_for(2)
    a.apply_sync(response(H, 0, 10))
    a.apply_sync(response(H, 8, 20, drop={14}))
    a.apply_sync(json.dumps(doc).encode())
    r = b.apply_sync(response(H, 0, 10))
    assert (r.added, r.already_known, r.gaps) == (10, 0, [])
    assert len(b.transactions()) == 10
    assert b.sync_gaps() == []
    assert b.possible_duplicates() == same_day_repeats(b)
    assert {t.id for t in a.transactions()}.isdisjoint(t.id for t in b.transactions())


def test_a_back_valued_csv_row_never_widens_coverage() -> None:
    # Verifier, #74. Bank order is not date order: the export holds rows 1-3
    # (6 Jan, a refund back-valued to 1 Jan, 12 Jan), so it is complete only from
    # 7 Jan, not from 2 Jan. A coffee bought 5 Jan (not in the export) posts on
    # 8 Jan in the sync, which starts on 8 Jan and misses the 6 Jan coffee. Two
    # real coffees: they must stay two, or be surfaced, never merged into one.
    ev = [
        (date(2026, 1, 5), "DEB", "CAFE NERO 0042", -350),
        (date(2026, 1, 6), "DEB", "CAFE NERO 0042", -350),
        (date(2026, 1, 1), "CR", "REFUND SHOP", 100),
        (date(2026, 1, 12), "DEB", "TFL TRAVEL", -200),
    ]
    h = History(SORT_CODE, "00000001", _with_balances(ev, 100000))
    with temp_ledger() as (led, d):
        csv = d / "export.csv"
        csv.write_text(render_export(h, 1, 4).text)
        led.import_files([csv])
        led.apply_sync(skewed_sync(h, [3, 0, 0, 0], 7, 10))
        (acc,) = [a for a in led.accounts() if a.masked_number == "••••0001"]
        led.link_synced_account("ACC-1", acc.id)
        coffees = [t for t in led.transactions() if "NERO" in t.description.upper()]
        assert len(coffees) == 2 or led.possible_duplicates()


def test_a_guessed_pairing_never_strands_a_later_csv_row() -> None:
    # Verifier, #74. Two real coffees: CSV 9 Jan / synced 10 Jan, and CSV 11 Jan /
    # synced 13 Jan. The first export starts after the 9 Jan coffee, so the 11 Jan
    # one may pair with either synced coffee: equal size, different rows left
    # over. Pairing it with 10 Jan strands the 9 Jan coffee a later export brings
    # (4 days from 13 Jan): three coffees, nothing surfaced. Equal size is not
    # enough; which rows stay unpaired must be fixed too.
    ev = [
        (date(2026, 1, 8), "DEB", "SHOP A", -100),
        (date(2026, 1, 9), "DEB", "CAFE NERO 0042", -350),
        (date(2026, 1, 9), "DEB", "TFL TRAVEL", -200),
        (date(2026, 1, 11), "DEB", "CAFE NERO 0042", -350),
        (date(2026, 1, 14), "DEB", "SHOP B", -300),
        (date(2026, 1, 16), "DEB", "SHOP C", -400),
    ]
    h = History(SORT_CODE, "00000001", _with_balances(ev, 100000))
    with temp_ledger() as (led, d):
        first, later = d / "first.csv", d / "later.csv"
        first.write_text(render_export(h, 2, 5).text)
        later.write_text(render_export(h, 0, 5).text)
        led.import_files([first])
        led.apply_sync(skewed_sync(h, [0, 1, 0, 2, 0, 0], 7, 15))
        (acc,) = [a for a in led.accounts() if a.masked_number == "••••0001"]
        led.link_synced_account("ACC-1", acc.id)
        led.import_files([later])
        coffees = {t.id for t in led.transactions() if "NERO" in t.description.upper()}
        surfaced = {i for g in led.possible_duplicates() for i in g}
        assert len(coffees) == 2 or coffees & surfaced


def test_hsbc_csv_links_to_a_sync_without_double_counting(
    ledger_for: LedgerFor, write_export: WriteExport
) -> None:
    """Verifier (#162): an HSBC CSV (no balance, no account number) linked to its
    synced Account merges the overlap like Lloyds does and lists no Gap."""
    from synthetic_banks import money

    rows = [
        f"{t.date:%d/%m/%Y},{t.description},{money(t.amount_minor)}"
        for t in H.txns[:30]
    ]
    led = ledger_for(1)
    led.import_files([write_export("# synthetic\n" + "\n".join(reversed(rows)) + "\n")])
    (csv_account,) = led.accounts()
    assert csv_account.bank == "HSBC" and csv_account.bank_balance is None
    led.link_synced_account("ACC-1", csv_account.id)
    led.apply_sync(response(H, 20, 30))
    r = led.apply_sync(response(H, 25, 40))
    assert r.gaps == [] and led.ambiguous_matches() == [] and led.gaps() == []
    assert len(led.transactions()) == 40
    assert sum(t.amount for t in led.transactions()) == Decimal(
        sum(t.amount_minor for t in H.txns[:40])
    ).scaleb(-2)
