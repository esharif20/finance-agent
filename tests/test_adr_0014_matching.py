"""Reference implementation of ADR 0014's CSV-to-sync matching rule, property-tested.

Not production code: it pins the ADR's wording so src/ can be tested against it.
"""

import unicodedata
from dataclasses import dataclass, replace
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

WINDOW = 3


@dataclass(frozen=True)
class Row:
    source: str  # "csv" or "synced"
    key: str  # provider ID for synced rows, record order (zero-padded) for CSV rows
    amount: int  # minor units
    currency: str
    description: str
    day: date


def normalise(description: str) -> str:
    folded = unicodedata.normalize("NFKC", description).casefold()
    return "".join(c for c in folded if unicodedata.category(c)[0] in "LN")


def candidates(a: Row, b: Row) -> bool:
    return (
        a.amount == b.amount
        and a.currency == b.currency
        and normalise(a.description) == normalise(b.description) != ""
        and abs((a.day - b.day).days) <= WINDOW
    )


Complete = dict[str, list[tuple[date, date]]]  # source -> runs of complete days


def components(rows: list[Row]) -> list[list[Row]]:
    """Connected components (size > 1) of the CSV-to-synced candidate graph."""
    parent = {r.key: r.key for r in rows}

    def find(k: str) -> str:
        while parent[k] != k:
            k = parent[k]
        return k

    for c in rows:
        for s in rows:
            if c.source == "csv" and s.source == "synced" and candidates(c, s):
                parent[find(c.key)] = find(s.key)
    out: dict[str, list[Row]] = {}
    for r in rows:
        out.setdefault(find(r.key), []).append(r)
    return [g for g in out.values() if len(g) > 1]


def must(r: Row, complete: Complete | None) -> bool:
    """The other source is complete on every day r's counterpart could have."""
    if complete is None:
        return True  # both sources complete everywhere
    other = complete["synced" if r.source == "csv" else "csv"]
    days = {r.day + timedelta(k) for k in range(-WINDOW, WINDOW + 1)}
    return all(any(a <= d <= b for a, b in other) for d in days)


def resolve(
    group: list[Row], complete: Complete | None
) -> list[tuple[str, str]] | None:
    """Pairs for one component, or None when ambiguous: by brute force, every
    matching that pairs each `must` row is an explanation; the component
    resolves only if one exists and all pair the same rows (so the same size)."""
    cs = sorted((r for r in group if r.source == "csv"), key=lambda r: (r.day, r.key))
    ss = sorted(
        (r for r in group if r.source == "synced"), key=lambda r: (r.day, r.key)
    )
    found: list[list[tuple[Row, Row]]] = []

    def walk(i: int, used: frozenset[str], acc: list[tuple[Row, Row]]) -> None:
        if i == len(cs):
            if all(s.key in used or not must(s, complete) for s in ss):
                found.append(acc)
            return
        c = cs[i]
        if not must(c, complete):
            walk(i + 1, used, acc)
        for s in ss:
            if s.key not in used and abs((c.day - s.day).days) <= WINDOW:
                walk(i + 1, used | {s.key}, [*acc, (c, s)])

    walk(0, frozenset(), [])
    # the same rows stay unpaired in every explanation, else the leftover is a guess
    if not found or len({frozenset(k for p in m for k in p) for m in found}) > 1:
        return None
    if len(found[0]) == len(cs) == len(ss):  # perfect: pair in date order
        return [(c.key, s.key) for c, s in zip(cs, ss, strict=True)]
    return [(c.key, s.key) for c, s in found[0]]


def match(
    rows: list[Row], complete: Complete | None = None
) -> tuple[list[tuple[str, str]], list[frozenset[str]]]:
    """Return (csv key, synced key) pairs and the ambiguous components."""
    pairs: list[tuple[str, str]] = []
    ambiguous: list[frozenset[str]] = []
    for group in components(rows):
        got = resolve(group, complete)
        if got is None:
            ambiguous.append(frozenset(r.key for r in group))
        else:
            pairs += got
    return pairs, ambiguous


BASE = date(2026, 1, 1)
descriptions = st.sampled_from(["CAFE NERO", "Tesco Store 12", "TFL TRAVEL", "!!!"])


@st.composite
def rows(draw: st.DrawFn) -> list[Row]:
    n_csv = draw(st.integers(0, 6))
    n_synced = draw(st.integers(0, 6))
    out: list[Row] = []
    for i in range(n_csv + n_synced):
        source = "csv" if i < n_csv else "synced"
        out.append(
            Row(
                source=source,
                key=f"c{i:03d}" if source == "csv" else f"s{i:03d}",
                amount=draw(st.sampled_from([-450, -450, -1299, 2000])),
                currency="GBP",
                description=draw(descriptions),
                day=BASE + timedelta(days=draw(st.integers(0, 10))),
            )
        )
    return out


def _by_key(rs: list[Row]) -> dict[str, Row]:
    return {r.key: r for r in rs}


@given(rows())
def test_pairs_are_one_to_one_valid_candidates(rs: list[Row]) -> None:
    pairs, ambiguous = match(rs)
    keyed = _by_key(rs)
    paired = [k for p in pairs for k in p]
    assert len(paired) == len(set(paired))
    for c, s in pairs:
        assert keyed[c].source == "csv" and keyed[s].source == "synced"
        assert candidates(keyed[c], keyed[s])
    # Ambiguous groups keep every row: none of them is paired.
    for group in ambiguous:
        assert not group & set(paired)
    # Every row is its own Transaction unless paired.
    assert len(rs) - len(pairs) == len({*keyed} - {s for _, s in pairs})


@given(rows())
def test_case_and_punctuation_do_not_change_matches(rs: list[Row]) -> None:
    def noisy(d: str) -> str:
        return (
            " -" + "".join(c.swapcase() + ("." if c.isalnum() else "") for c in d) + "*"
        )

    pairs, ambiguous = match(rs)
    assert match([replace(r, description=noisy(r.description)) for r in rs]) == (
        pairs,
        ambiguous,
    )


@given(
    st.lists(st.integers(0, 30), min_size=1, max_size=6),
    st.integers(0, WINDOW),
)
def test_equal_count_groups_pair_in_date_order(days: list[int], lag: int) -> None:
    csv = [
        Row("csv", f"c{i:03d}", -450, "GBP", "CAFE NERO", BASE + timedelta(d))
        for i, d in enumerate(days)
    ]
    synced = [
        Row("synced", f"s{i:03d}", -450, "GBP", "Cafe Nero", BASE + timedelta(d + lag))
        for i, d in enumerate(days)
    ]
    pairs, ambiguous = match(csv + synced)
    expected = list(
        zip(
            [r.key for r in sorted(csv, key=lambda r: (r.day, r.key))],
            [r.key for r in sorted(synced, key=lambda r: (r.day, r.key))],
            strict=True,
        )
    )
    assert ambiguous == []
    assert sorted(pairs) == sorted(expected)


def test_provider_sending_one_purchase_three_times_keeps_all_rows() -> None:
    csv = Row("csv", "c000", -1299, "GBP", "AMAZON", BASE)
    synced = [
        Row("synced", pid, -1299, "GBP", "Amazon", BASE) for pid in ("007", "7", "0007")
    ]
    pairs, ambiguous = match([csv, *synced])
    assert pairs == []
    assert ambiguous == [frozenset({"c000", "007", "7", "0007"})]


def test_chain_beyond_the_window_is_ambiguous_not_merged() -> None:
    # Dates D, D+3, D+6 form one component; the sorted pairing is checked pairwise.
    rs = [
        Row("csv", "c000", -450, "GBP", "TFL", BASE),
        Row("synced", "s1", -450, "GBP", "TFL", BASE + timedelta(3)),
        Row("csv", "c001", -450, "GBP", "TFL", BASE + timedelta(6)),
    ]
    assert match(rs) == ([], [frozenset({"c000", "s1", "c001"})])


def test_empty_normalised_description_never_matches() -> None:
    rs = [
        Row("csv", "c000", -450, "GBP", "***", BASE),
        Row("synced", "s1", -450, "GBP", "--", BASE),
    ]
    assert match(rs) == ([], [])


def test_a_chain_across_the_coverage_boundary_is_ambiguous_not_merged() -> None:
    # #74: coffees on 1, 3 and 5 Jan; the CSV holds 1-3 Jan, the sync 3-5 Jan.
    # Sorted pairing gives (1, 3) and (3, 5): two purchases instead of three.
    # The 1 Jan row's counterpart could predate the sync, so it is ambiguous.
    day = [BASE + timedelta(d) for d in range(6)]
    rs = [
        Row("csv", "c000", -350, "GBP", "CAFE NERO", day[0]),
        Row("csv", "c001", -350, "GBP", "CAFE NERO", day[2]),
        Row("synced", "s1", -350, "GBP", "Cafe Nero", day[2]),
        Row("synced", "s2", -350, "GBP", "Cafe Nero", day[4]),
    ]
    complete = {"csv": [(day[1], day[1])], "synced": [(day[2], day[3])]}
    assert match(rs) == ([("c000", "s1"), ("c001", "s2")], [])  # the old rule
    assert match(rs, complete) == ([], [frozenset({"c000", "c001", "s1", "s2"})])


def test_a_row_the_other_source_must_hold_is_paired_near_the_boundary() -> None:
    # The sync is complete around the CSV row, so the CSV row must have its
    # counterpart: the only candidate. The synced row may not, which is fine.
    rs = [
        Row("csv", "c000", -350, "GBP", "CAFE NERO", BASE + timedelta(5)),
        Row("synced", "s1", -350, "GBP", "Cafe Nero", BASE + timedelta(6)),
    ]
    complete = {
        "csv": [(BASE, BASE + timedelta(5))],
        "synced": [(BASE, BASE + timedelta(20))],
    }
    assert match(rs, complete) == ([("c000", "s1")], [])


@st.composite
def coverage(draw: st.DrawFn) -> Complete:
    def runs() -> list[tuple[date, date]]:
        out: list[tuple[date, date]] = []
        for _ in range(draw(st.integers(0, 2))):
            a, b = sorted(draw(st.integers(-4, 14)) for _ in range(2))
            out.append((BASE + timedelta(a), BASE + timedelta(b)))
        return out

    return {"csv": runs(), "synced": runs()}


@given(rows(), coverage())
def test_src_resolves_exactly_the_components_the_reference_does(
    rs: list[Row], complete: Complete
) -> None:
    from finance_agent.ledger import (
        _MatchRow,  # pyright: ignore[reportPrivateUsage]
        _pair,  # pyright: ignore[reportPrivateUsage]
        _within,  # pyright: ignore[reportPrivateUsage]
    )

    for group in components(rs):
        ids = {r.key: i for i, r in enumerate(group)}
        src = [_MatchRow(ids[r.key], r.source == "synced", r.day, r.key) for r in group]
        need = {
            ids[r.key]
            for r in group
            if _within(complete["synced" if r.source == "csv" else "csv"], r.day)
        }
        assert need == {ids[r.key] for r in group if must(r, complete)}
        ref, got = resolve(group, complete), _pair(src, need)
        assert (ref is None) == (got is None)
        if ref is not None and got is not None:
            assert len(ref) == len(got)
            if len(ref) == len(group) / 2:  # perfect: the same date-order pairs
                assert sorted(got) == sorted((ids[c], ids[s]) for c, s in ref)


def gap(b1: int, bk: int, ledger: list[tuple[int, int]], t1: int, tk: int) -> int:
    """ADR 0014 Reconciliation Gap; ledger holds (instant, amount)."""
    return bk - b1 - sum(a for t, a in ledger if t1 < t <= tk)


@given(
    st.lists(st.tuples(st.integers(0, 100), st.integers(-10_000, 10_000)), min_size=1),
    st.integers(-50_000, 50_000),
    st.data(),
)
def test_missing_row_shows_as_gap_equal_to_its_amount(
    feed: list[tuple[int, int]], opening: int, data: st.DataObject
) -> None:
    def balance(t: int) -> int:
        return opening + sum(a for ti, a in feed if ti <= t)

    t1, tk = 20, 100
    assert gap(balance(t1), balance(tk), feed, t1, tk) == 0
    lost = data.draw(st.sampled_from(feed))
    missing = list(feed)
    missing.remove(lost)
    expected = lost[1] if t1 < lost[0] <= tk else 0
    assert gap(balance(t1), balance(tk), missing, t1, tk) == expected
    assert gap(balance(t1), balance(tk), feed + [lost], t1, tk) == -expected
