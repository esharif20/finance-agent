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
from typing import Any

from sqlalchemy import Connection, Select, case, func, select, union

from finance_agent import ingest
from finance_agent.storage import Scoped, schema

TRANSFER_TYPES = ("TFR",)  # Lloyds Type that favours a pair; not required
TRANSFER_DAYS = 3  # the two legs post at most this many days apart


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


log = logging.getLogger(__name__)  # counts, IDs and timings only


@dataclass(frozen=True)
class Categorisation:
    kind: str  # Spending, Income, Transfer, Refund or Fee
    merchant: str | None
    category: tuple[str, str] | None  # (top level, subcategory)
    source: str  # "merchant:<name>", "rule:<id>", "none"; "" without a Category
    taxonomy_version: int


@dataclass(frozen=True)
class Transaction:
    posted_on: date
    description: str
    amount: Decimal
    running_balance: Decimal
    statement_imports: frozenset[str]  # file hashes of every import it appeared in
    id: int = 0
    bank_type: str = ""  # the Bank's Transaction Type code; not the Kind
    transfer_with: int | None = None  # the other leg of an own-account Transfer
    categorisation: Categorisation | None = None  # None until `categorise.run`


@dataclass(frozen=True)
class Spend:
    """Spending minus Refunds in one Category, Subcategory or Merchant;
    negative when Refunds received exceed spending (ADR 0004)."""

    name: str
    amount: Decimal
    children: tuple["Spend", ...] = ()


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
        added = known = 0
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
            for row in parsed.rows:
                account_id = self._account_id(bank_id, row)
                txn_id = self._known_transaction_id(account_id, row, import_id)
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
        if added:
            self._link_transfers()
        return ImportReport(
            added,
            known,
            rejected,
            [AffectedAccount(i, m) for i, m in sorted(accounts.items())],
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
                t.c.bank_type,
            ).order_by(t.c.posted_on, t.c.id)
        ).all()
        other_leg = self._transfer_legs()
        cats = self._categorisations()
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
                r.id,
                r.bank_type,
                other_leg.get(r.id),
                cats.get(r.id),
            )
            for r in rows
        ]

    def spending_by_category(self, month: str) -> list[Spend]:
        """Spending minus Refunds received in `month` (ADR 0004), by Category,
        then Subcategory, then Merchant ("Unknown" when none), largest first."""
        start, end = _month_bounds(month)
        t = schema.transaction
        amounts: dict[int, int] = {
            r.id: r.amount_minor
            for r in self._db.execute(
                self._db.select(t, t.c.id, t.c.amount_minor).where(
                    t.c.posted_on >= start,
                    t.c.posted_on < end,
                    t.c.currency == "GBP",  # shortcut: as in summarise_month
                )
            )
        }
        tree: dict[str, dict[str, dict[str, int]]] = {}
        for txn_id, c in self._categorisations().items():
            if txn_id in amounts and c.category and c.kind in ("Spending", "Refund"):
                top, sub = c.category
                merchants = tree.setdefault(top, {}).setdefault(sub, {})
                key = c.merchant or "Unknown"
                merchants[key] = merchants.get(key, 0) - amounts[txn_id]

        def level(nodes: dict[str, Any]) -> tuple[Spend, ...]:
            out: list[Spend] = []
            for name, v in nodes.items():
                if isinstance(v, int):  # a Merchant's minor-unit total
                    out.append(Spend(name, _decimal(v)))
                else:
                    kids = level(v)
                    total = sum((k.amount for k in kids), Decimal(0))
                    out.append(Spend(name, total, kids))
            return tuple(sorted(out, key=lambda s: (-s.amount, s.name)))

        return list(level(tree))

    def _categorisations(self) -> dict[int, Categorisation]:
        c, sub, top = (
            schema.categorisation,
            schema.category.alias("sub"),
            schema.category.alias("top"),
        )
        rows = self._db.execute(
            self._db.select(
                c,
                c.c.transaction_id,
                c.c.kind,
                c.c.merchant,
                c.c.source,
                c.c.taxonomy_version,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
            )
            .outerjoin(sub, sub.c.id == c.c.category_id)
            .outerjoin(top, top.c.id == sub.c.parent_id)
        )
        return {
            r.transaction_id: Categorisation(
                r.kind,
                r.merchant,
                (r.top, r.sub) if r.sub is not None else None,
                r.source,
                r.taxonomy_version,
            )
            for r in rows
        }

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
            t.c.id.not_in(self._transfer_ids()),  # neither spending nor income
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

    def _transfer_ids(self) -> Select[Any]:
        """Ids of every Transaction that is one leg of a Transfer."""
        tl = schema.transfer_link
        return (
            union(
                self._db.select(tl, tl.c.from_transaction_id),
                self._db.select(tl, tl.c.to_transaction_id),
            )
            .subquery()
            .select()
        )

    def _transfer_legs(self) -> dict[int, int]:
        tl = schema.transfer_link
        legs: dict[int, int] = {}
        for r in self._db.execute(
            self._db.select(tl, tl.c.from_transaction_id, tl.c.to_transaction_id)
        ):
            a, b = int(r.from_transaction_id), int(r.to_transaction_id)
            legs[a], legs[b] = b, a
        return legs

    def _link_transfers(self) -> None:
        """Pair unlinked own-account Transfer legs: same amount and currency,
        opposite sign, another of this user's Accounts, dates within
        TRANSFER_DAYS. Among candidates, Type TFR on both legs, then identical
        descriptions, then the closest date win; each leg pairs at most once.

        Uses raw Bank fields only.
        """
        t = schema.transaction
        linked = self._transfer_legs()
        rows = [
            r
            for r in self._db.execute(
                self._db.select(
                    t,
                    t.c.id,
                    t.c.account_id,
                    t.c.posted_on,
                    t.c.bank_type,
                    t.c.description,
                    t.c.amount_minor,
                    t.c.currency,
                ).order_by(t.c.posted_on, t.c.id)
            )
            if r.id not in linked
        ]
        incoming: dict[tuple[str, int], list[Any]] = {}
        for r in rows:
            if r.amount_minor > 0:
                incoming.setdefault((r.currency, r.amount_minor), []).append(r)
        candidates = [
            (
                (
                    not (
                        o.bank_type in TRANSFER_TYPES and i.bank_type in TRANSFER_TYPES
                    ),
                    o.description != i.description,
                    abs((i.posted_on - o.posted_on).days),
                    o.id,
                    i.id,
                ),
                o.id,
                i.id,
            )
            for o in rows
            if o.amount_minor < 0
            for i in incoming.get((o.currency, -o.amount_minor), [])
            if i.account_id != o.account_id
            and abs((i.posted_on - o.posted_on).days) <= TRANSFER_DAYS
        ]
        used: set[int] = set()
        for _, out_id, in_id in sorted(candidates):  # best pairs claim legs first
            if out_id in used or in_id in used:
                continue
            used |= {out_id, in_id}
            self._db.insert(
                schema.transfer_link,
                from_transaction_id=out_id,
                to_transaction_id=in_id,
            )

    def _known_transaction_id(
        self, account_id: int, row: ingest.ParsedRow, import_id: int
    ) -> int | None:
        """ADR 0006 identity: (Account, date, description, amount, balance).

        Rows within one file are distinct Transactions, so a match must not
        already be linked to this import (keeps identical-key twins apart).
        """
        t, src = schema.transaction, schema.transaction_source
        in_this_import = (
            select(src.c.transaction_id)
            .where(src.c.statement_import_id == import_id)
            .where(src.c.transaction_id == t.c.id)
        )
        found = self._db.execute(
            self._db.select(t, t.c.id)
            .where(
                t.c.account_id == account_id,
                t.c.posted_on == row.posted_on,
                t.c.description == row.description,
                t.c.amount_minor == row.amount_minor,
                t.c.running_balance_minor == row.balance_minor,
                ~in_this_import.exists(),
            )
            .order_by(t.c.id)
            .limit(1)
        ).scalar()
        return None if found is None else int(found)

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
