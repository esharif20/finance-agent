"""The Ledger application interface: the one in-process seam used by the CLI,
the API and the tests. Takes a connection from `storage.open_db` and a user_id;
never returns floats."""

import hashlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, case, func

from finance_agent import ingest
from finance_agent.storage import Scoped, schema


@dataclass(frozen=True)
class Rejection:
    file: str
    line: int
    reason: str


@dataclass(frozen=True)
class AffectedAccount:
    id: int
    masked_number: str  # never the full number


@dataclass(frozen=True)
class ImportReport:
    added: int
    already_known: int
    rejected: list[Rejection]  # one per refused file; nothing of it is stored
    accounts: list[AffectedAccount]  # Accounts that gained Transactions
    # rows matched to stored rows that fit at more than one position, such as
    # one row that is either of two twins (ADR 0006); the earliest is used
    ambiguous: int = 0


log = logging.getLogger(__name__)  # counts, IDs and timings only

Identity = tuple[date, str, int, int]


def row_identity(row: ingest.ParsedRow) -> Identity:
    """ADR 0006: raw Bank fields as exported, never anything derived."""
    return (row.posted_on, row.description, row.amount_minor, row.balance_minor)


def _merge(a: list[int], b: list[int]) -> list[int] | None:
    """Join two Bank-ordered runs of Transaction ids that overlap consistently."""
    if b[0] in a:
        i = a.index(b[0])
        if a[i : i + len(b)] != b[: len(a) - i]:
            return None
        return a[:i] + b if i + len(b) > len(a) else a
    return _merge(b, a) if a[0] in b else None


def _best_overlaps(new: list[Identity], run: list[Identity]) -> tuple[int, list[int]]:
    """Longest overlap of `new` with `run` as one contiguous block, and every
    offset (new[i] is run[i + offset]) that achieves it."""
    best, offsets = 0, list[int]()
    for o in range(1 - len(new), len(run)):
        lo, hi = max(0, -o), min(len(new), len(run) - o)
        if hi - lo >= best and all(new[i] == run[i + o] for i in range(lo, hi)):
            if hi - lo > best:
                best, offsets = hi - lo, []
            offsets.append(o)
    return best, offsets


@dataclass(frozen=True)
class Transaction:
    posted_on: date
    description: str
    amount: Decimal
    running_balance: Decimal
    statement_imports: frozenset[str]  # file hashes of every import it appeared in


@dataclass(frozen=True)
class MonthSummary:
    month: str  # YYYY-MM
    currency: str
    money_in: Decimal
    money_out: Decimal  # positive
    net: Decimal


def _decimal(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)


def _month_bounds(month: str) -> tuple[date, date]:
    start = datetime.strptime(month, "%Y-%m").date()
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


class Ledger:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def import_files(self, paths: Sequence[Path]) -> ImportReport:
        added = known = ambiguous = 0
        rejected: list[Rejection] = []
        accounts: dict[int, str] = {}
        for n, path in enumerate(paths, 1):
            started = time.perf_counter_ns()
            data = path.read_bytes()
            try:
                parsed = ingest.parse(data)
            except ingest.UnreadableFile as e:
                rejected.append(Rejection(path.name, e.line, e.reason))
                log.info("import file=%d rejected=1", n)
                continue
            if parsed.rejected:  # one bad row refuses the whole file
                rejected += [
                    Rejection(path.name, r.line, r.reason) for r in parsed.rejected
                ]
                log.info("import file=%d rejected=%d", n, len(parsed.rejected))
                continue
            digest = hashlib.sha256(data).hexdigest()
            si = schema.statement_import
            if self._db.execute(
                self._db.select(si, si.c.id).where(si.c.file_hash == digest)
            ).first():
                known += len(parsed.rows)
                log.info("import file=%d already_known=%d", n, len(parsed.rows))
                continue
            import_id = self._db.insert(
                si,
                source="csv",
                file_hash=digest,
                original_bytes=data,
                imported_at=datetime.now(UTC),
            )
            bank_id = self._bank_id(parsed.bank)
            file_added = 0
            by_account: dict[int, list[ingest.ParsedRow]] = {}
            for row in parsed.rows:
                by_account.setdefault(self._account_id(bank_id, row), []).append(row)
            known_ids: dict[int, int] = {}  # row line -> stored Transaction id
            for account_id, rows in by_account.items():
                aligned, unsure = self._align(account_id, rows)
                known_ids |= aligned
                ambiguous += unsure
            for row in parsed.rows:
                account_id = self._account_id(bank_id, row)
                txn_id = known_ids.get(row.line)
                if txn_id is None:
                    txn_id = self._db.insert(
                        schema.transaction,
                        account_id=account_id,
                        posted_on=row.posted_on,
                        bank_type=row.bank_type,
                        description=row.description,
                        amount_minor=row.amount_minor,
                        currency=row.currency,
                        running_balance_minor=row.balance_minor,
                        status="completed",
                    )
                    accounts[account_id] = row.masked_number
                    file_added += 1
                else:
                    known += 1
                self._db.insert(
                    schema.transaction_source,
                    transaction_id=txn_id,
                    statement_import_id=import_id,
                    row_number=row.line,
                )
            added += file_added
            log.info(
                "import file=%d statement_import_id=%d added=%d ms=%d",
                n,
                import_id,
                file_added,
                (time.perf_counter_ns() - started) // 1_000_000,
            )
        return ImportReport(
            added,
            known,
            rejected,
            [AffectedAccount(i, m) for i, m in sorted(accounts.items())],
            ambiguous,
        )

    def transactions(self) -> list[Transaction]:
        """Every stored Transaction, oldest first."""
        t, src, si = (
            schema.transaction,
            schema.transaction_source,
            schema.statement_import,
        )
        rows = self._db.execute(
            self._db.select(
                t,
                t.c.id,
                t.c.posted_on,
                t.c.description,
                t.c.amount_minor,
                t.c.running_balance_minor,
            ).order_by(t.c.posted_on, t.c.id)
        ).all()
        hashes: dict[int, set[str]] = {}
        for link in self._db.execute(
            self._db.select(src, src.c.transaction_id, si.c.file_hash).join(
                si, si.c.id == src.c.statement_import_id
            )
        ):
            hashes.setdefault(link.transaction_id, set()).add(link.file_hash)
        return [
            Transaction(
                r.posted_on,
                r.description,
                _decimal(r.amount_minor),
                _decimal(r.running_balance_minor),
                frozenset(hashes.get(r.id, ())),
            )
            for r in rows
        ]

    def summarise_month(self, month: str) -> MonthSummary:
        start, end = _month_bounds(month)
        t = schema.transaction
        amt = t.c.amount_minor
        stmt = self._db.select(
            t,
            func.coalesce(func.sum(case((amt > 0, amt), else_=0)), 0),
            func.coalesce(func.sum(case((amt < 0, -amt), else_=0)), 0),
        ).where(
            t.c.posted_on >= start,
            t.c.posted_on < end,
            # shortcut: GBP only until FX conversion lands; then sum gbp_minor
            t.c.currency == "GBP",
        )
        row = self._db.execute(stmt).one()
        ins, outs = (int(v) for v in row)
        return MonthSummary(
            month,
            "GBP",
            _decimal(ins),
            _decimal(outs),
            _decimal(ins - outs),
        )

    def _align(
        self, account_id: int, rows: list[ingest.ParsedRow]
    ) -> tuple[dict[int, int], int]:
        """ADR 0006: place a new file's rows (one Account, Bank order) against the
        stored runs. A file is a contiguous window of the Bank's history, so its
        overlap with each run is one contiguous block; the longest consistent
        block wins. Returns ({row line: Transaction id}, rows placed ambiguously)."""
        new = [row_identity(r) for r in rows]
        runs, identity = self._bank_order_runs(account_id)
        found: list[tuple[int, int, list[int]]] = []
        for n, run in enumerate(runs):
            length, offsets = _best_overlaps(new, [identity[t] for t in run])
            if length:
                found.append((length, n, offsets))
        placed: dict[int, int] = {}
        unsure = 0
        for length, n, offsets in sorted(found, key=lambda f: -f[0]):
            run, o = runs[n], offsets[0]
            span = range(max(0, -o), min(len(new), len(run) - o))
            if any(i in placed for i in span):
                continue  # a longer block already placed these rows
            placed |= {i: run[i + o] for i in span}
            if len(offsets) > 1:  # e.g. one row that is either of two twins
                unsure += length
        return {rows[i].line: t for i, t in placed.items()}, unsure

    def _bank_order_runs(
        self, account_id: int
    ) -> tuple[list[list[int]], dict[int, Identity]]:
        """The Account's stored Transactions as runs in Bank order: each Statement
        Import's rows, joined where they share Transactions. Runs are separated
        where no import covers the gap between them."""
        t, src = schema.transaction, schema.transaction_source
        links = self._db.execute(
            self._db.select(
                src,
                src.c.statement_import_id,
                t.c.id,
                t.c.posted_on,
                t.c.description,
                t.c.amount_minor,
                t.c.running_balance_minor,
            )
            .join(t, t.c.id == src.c.transaction_id)
            .where(t.c.account_id == account_id)
            .order_by(src.c.statement_import_id, src.c.row_number)
        ).all()
        identity: dict[int, Identity] = {}
        per_import: dict[int, list[int]] = {}
        for r in links:
            identity[r.id] = (
                r.posted_on,
                r.description,
                r.amount_minor,
                r.running_balance_minor,
            )
            per_import.setdefault(r.statement_import_id, []).append(r.id)
        runs: list[list[int]] = []
        for run in per_import.values():
            merged = True
            while merged:
                merged = False
                for other in runs:
                    if (joined := _merge(other, run)) is not None:
                        runs.remove(other)
                        run, merged = joined, True
                        break
            runs.append(run)
        # shortcut: rebuilt per file, O(stored rows x file rows); persist runs if
        # imports get slow
        return runs, identity

    def _bank_id(self, name: str) -> int:
        b = schema.bank
        found = self._db.execute(
            self._db.select(b, b.c.id).where(b.c.name == name)
        ).scalar()
        if found is not None:
            return int(found)
        return self._db.insert(b, name=name)

    def _account_id(self, bank_id: int, row: ingest.ParsedRow) -> int:
        a = schema.account
        found = self._db.execute(
            self._db.select(a, a.c.id).where(a.c.external_ref == row.account_ref)
        ).scalar()
        if found is not None:
            return int(found)
        new = self._db.insert(
            a,
            bank_id=bank_id,
            external_ref=row.account_ref,
            name=f"Account {row.masked_number}",
            kind="current",
            masked_number=row.masked_number,
            currency=row.currency,
        )
        return new
