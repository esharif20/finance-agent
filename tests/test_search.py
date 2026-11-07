"""Ledger.search_transactions: the filters behind /api/transactions."""

import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from finance_agent import categorise
from finance_agent.ledger import Ledger, Transaction
from finance_agent.storage import open_db
from synthetic_lloyds import generate_histories, render_export


@contextmanager
def two_accounts() -> Generator[Ledger]:
    """Current and saver Accounts, categorised, with one Correction."""
    current, saver = generate_histories(seed=11, days=45)
    with tempfile.TemporaryDirectory() as d:
        paths: list[Path] = []
        for i, h in enumerate((current, saver)):
            p = Path(d) / f"e{i}.csv"
            p.write_text(render_export(h, 0, min(150, len(h.txns))).text)
            paths.append(p)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            ledger.import_files(paths)
            categorise.run(conn, 1)
            spend = next(
                t
                for t in ledger.transactions()
                if t.categorisation and t.categorisation.kind == "Spending"
            )
            ledger.correct_transaction(spend.id, ("Travel", "Hotels"))
            yield ledger
        engine.dispose()


def expected(
    ledger: Ledger,
    account_id: int | None,
    kind: str | None,
    category: str | None,
    month: str | None,
    q: str | None,
) -> list[Transaction]:
    """The plain reading of each filter over every Transaction, newest first."""
    needle = (q or "").strip().lower()
    out: list[Transaction] = []
    for t in reversed(ledger.transactions()):
        c = t.categorisation
        if account_id is not None and t.account_id != account_id:
            continue
        if month is not None and t.posted_on.strftime("%Y-%m") != month:
            continue
        if kind is not None and (c is None or c.kind != kind):
            continue
        if category is not None and (
            c is None or not c.category or c.category[0] != category
        ):
            continue
        merchant = (c.merchant if c else None) or ""
        if (
            needle
            and needle not in t.description.lower()
            and needle not in merchant.lower()
        ):
            continue
        out.append(t)
    return out


with two_accounts() as _l:
    _TXNS = _l.transactions()
    MONTHS = sorted({f"{t.posted_on:%Y-%m}" for t in _TXNS})
    WORDS = sorted(
        {w.lower() for t in _TXNS for w in t.description.split()[:1]}
        | {
            t.categorisation.merchant.lower()[:4]
            for t in _TXNS
            if t.categorisation and t.categorisation.merchant
        }
    )
    ACCOUNTS = sorted({t.account_id for t in _TXNS})


@pytest.fixture(scope="module")
def ledger() -> Generator[Ledger]:  # read-only below, so shared
    with two_accounts() as led:
        yield led


@settings(
    max_examples=80,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    account_id=st.none() | st.sampled_from([*ACCOUNTS, 999]),
    kind=st.none()
    | st.sampled_from(["Spending", "Income", "Transfer", "Refund", "Fee"]),
    category=st.none() | st.sampled_from([*categorise.TAXONOMY, "Nope"]),
    month=st.none() | st.sampled_from(MONTHS),
    q=st.none() | st.sampled_from([*WORDS, "%", "_", "  ", "zzz"]),
    limit=st.integers(1, 40),
    offset=st.integers(0, 60),
)
def test_search_matches_each_filter_and_pages_newest_first(
    ledger: Ledger,
    account_id: int | None,
    kind: str | None,
    category: str | None,
    month: str | None,
    q: str | None,
    limit: int,
    offset: int,
) -> None:
    want = expected(ledger, account_id, kind, category, month, q)
    got = ledger.search_transactions(
        account_id=account_id,
        kind=kind,
        category=category,
        month=month,
        query=q,
        limit=limit,
        offset=offset,
    )
    assert got.total == len(want)
    assert got.items == want[offset : offset + limit]


def test_search_sees_the_correction_overlay(ledger: Ledger) -> None:
    page = ledger.search_transactions(category="Travel", limit=500)
    assert any(
        t.categorisation and t.categorisation.source == "correction" for t in page.items
    )


def test_like_wildcards_in_the_query_are_literal(ledger: Ledger) -> None:
    assert ledger.search_transactions(query="%").total == 0
    assert ledger.search_transactions(query="_").total == 0


def test_search_never_returns_another_users_rows(ledger: Ledger) -> None:
    other = Ledger(ledger._db.conn, 2)  # pyright: ignore[reportPrivateUsage]
    assert other.search_transactions(limit=500).total == 0
