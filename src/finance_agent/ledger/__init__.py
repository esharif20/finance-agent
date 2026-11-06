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
    source: str  # "merchant:<name>", "rule:<id>" or "default"
    taxonomy_version: int
    merchant_id: int | None = None


@dataclass(frozen=True)
class Merchant:
    id: int  # stable across imports and rule changes
    name: str
    default_category: tuple[str, str] | None


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
class Gap:
    """A Reconciliation Gap: the Bank's balance disagrees with the Ledger's
    between two Transactions. The Bank is truth, so `amount` is the net of
    whatever is missing (positive: money in is missing)."""

    account: AffectedAccount
    from_date: date  # last Transaction that agrees
    to_date: date  # first Transaction after the break
    amount: Decimal


@dataclass(frozen=True)
class AccountBalance:
    id: int
    masked_number: str
    name: str
    kind: str
    currency: str
    bank_balance: Decimal  # the Bank's last running balance (the truth)
    ledger_balance: Decimal  # opening balance plus every Transaction we hold
    gaps: list[Gap]  # bank_balance - ledger_balance == sum of these amounts


@dataclass(frozen=True)
class MonthSummary:
    month: str  # YYYY-MM
    currency: str
    money_in: Decimal
    money_out: Decimal  # positive
    net: Decimal
    gaps: list[Gap]  # Gaps touching the month: the figures may be incomplete


@dataclass(frozen=True)
class BankOrderRow:
    id: int
    posted_on: date
    description: str
    amount_minor: int
    running_balance_minor: int


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
            for position, row in enumerate(parsed.rows):
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
                    bank_position=position,
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
        self._recompute_gaps()
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

    def merchants(self) -> list[Merchant]:
        """Every Merchant of this user with its default Category, by name."""
        m, sub, top = (
            schema.merchant,
            schema.category.alias("sub"),
            schema.category.alias("top"),
        )
        rows = self._db.execute(
            self._db.select(
                m,
                m.c.id,
                m.c.name,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
            )
            .outerjoin(sub, sub.c.id == m.c.default_category_id)
            .outerjoin(top, top.c.id == sub.c.parent_id)
            .order_by(m.c.name)
        )
        return [
            Merchant(r.id, r.name, (r.top, r.sub) if r.sub is not None else None)
            for r in rows
        ]

    def _categorisations(self) -> dict[int, Categorisation]:
        c, m, sub, top = (
            schema.categorisation,
            schema.merchant,
            schema.category.alias("sub"),
            schema.category.alias("top"),
        )
        rows = self._db.execute(
            self._db.select(
                c,
                c.c.transaction_id,
                c.c.kind,
                c.c.merchant_id,
                m.c.name.label("merchant"),
                c.c.source,
                c.c.taxonomy_version,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
            )
            .outerjoin(m, m.c.id == c.c.merchant_id)
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
                r.merchant_id,
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
            [g for g in self.gaps() if g.from_date < end and g.to_date >= start],
        )

    def latest_month(self) -> str | None:
        """The month (YYYY-MM) of the newest Transaction, or None if empty."""
        t = schema.transaction
        newest = self._db.execute(self._db.select(t, func.max(t.c.posted_on))).scalar()
        return None if newest is None else f"{newest:%Y-%m}"

    def months(self) -> list[str]:
        """Months (YYYY-MM) that have Transactions, newest first."""
        t = schema.transaction
        days = self._db.execute(self._db.select(t, t.c.posted_on).distinct()).scalars()
        return sorted({f"{d:%Y-%m}" for d in days}, reverse=True)

    def accounts(self) -> list[AccountBalance]:
        """Accounts with Bank and Ledger balances; an empty Account is skipped."""
        a = schema.account
        gaps = self.gaps()
        out: list[AccountBalance] = []
        for r in self._db.execute(
            self._db.select(
                a, a.c.id, a.c.masked_number, a.c.name, a.c.kind, a.c.currency
            ).order_by(a.c.id)
        ).all():
            rows = self._transactions_in_bank_order(r.id)
            if not rows:
                continue
            opening = rows[0].running_balance_minor - rows[0].amount_minor
            out.append(
                AccountBalance(
                    r.id,
                    r.masked_number,
                    r.name,
                    r.kind,
                    r.currency,
                    _decimal(rows[-1].running_balance_minor),
                    _decimal(opening + sum(x.amount_minor for x in rows)),
                    [g for g in gaps if g.account.id == r.id],
                )
            )
        return out

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

    def gaps(self) -> list[Gap]:
        """Open Reconciliation Gaps, by Account then date."""
        g, a = schema.reconciliation_gap, schema.account
        rows = self._db.execute(
            self._db.select(
                g,
                g.c.from_date,
                g.c.to_date,
                g.c.amount_minor,
                a.c.id,
                a.c.masked_number,
            )
            .join(a, a.c.id == g.c.account_id)
            .order_by(a.c.id, g.c.from_date, g.c.id)
        ).all()
        return [
            Gap(
                AffectedAccount(r.id, r.masked_number),
                r.from_date,
                r.to_date,
                _decimal(r.amount_minor),
            )
            for r in rows
        ]

    def _recompute_gaps(self) -> None:
        """Walk each Account in Bank order; every break in the running balance
        is a Gap. Rebuilt from scratch, so a Gap closes once its rows arrive."""
        g, a = schema.reconciliation_gap, schema.account
        self._db.execute(self._db.delete(g))
        for account_id in self._db.execute(self._db.select(a, a.c.id)).scalars():
            prev: BankOrderRow | None = None
            for t in self._transactions_in_bank_order(account_id):
                bank_prior = t.running_balance_minor - t.amount_minor
                if prev is not None and bank_prior != prev.running_balance_minor:
                    self._db.insert(
                        g,
                        account_id=account_id,
                        from_date=prev.posted_on,
                        to_date=t.posted_on,
                        amount_minor=bank_prior - prev.running_balance_minor,
                    )
                prev = t
        log.info("reconcile gaps=%d", len(self.gaps()))

    def _transactions_in_bank_order(self, account_id: int) -> list[BankOrderRow]:
        """One Account's Transactions in the order the Bank listed them.

        Each Statement Import is a contiguous run of Bank order, so its rows give
        "comes before" edges; the union is topologically sorted, earliest date
        first. Ties (rows split across files that don't overlap) go to the row
        whose prior balance continues the current one, then to the row the same
        file lists next, then to storage order.
        """
        t, src = schema.transaction, schema.transaction_source
        rows = {
            r.id: BankOrderRow(
                r.id,
                r.posted_on,
                r.description,
                r.amount_minor,
                r.running_balance_minor,
            )
            for r in self._db.execute(
                self._db.select(
                    t,
                    t.c.id,
                    t.c.posted_on,
                    t.c.description,
                    t.c.amount_minor,
                    t.c.running_balance_minor,
                ).where(t.c.account_id == account_id)
            )
        }
        after: dict[int, set[int]] = {i: set() for i in rows}
        preds = dict.fromkeys(rows, 0)
        last: dict[int, int] = {}  # statement_import_id -> previous row's txn
        for link in self._db.execute(
            self._db.select(src, src.c.transaction_id, src.c.statement_import_id)
            .where(src.c.transaction_id.in_(rows))
            .order_by(src.c.statement_import_id, src.c.bank_position, src.c.row_number)
        ):
            before = last.get(link.statement_import_id)
            if before is not None and link.transaction_id not in after[before]:
                after[before].add(link.transaction_id)
                preds[link.transaction_id] += 1
            last[link.statement_import_id] = link.transaction_id
        ready = {i for i, n in preds.items() if n == 0}
        out: list[BankOrderRow] = []
        placed: set[int] = set()
        balance: int | None = None
        follows: set[int] = set()  # rows listed right after the last pick
        while len(out) < len(rows):
            if not ready:  # shortcut: inconsistent files form a cycle; break it
                ready = {
                    min(
                        set(rows) - placed,
                        key=lambda i: (rows[i].posted_on, i),
                    )
                }
            day = min(rows[i].posted_on for i in ready)
            pick = min(
                (i for i in ready if rows[i].posted_on == day),
                key=lambda i: (
                    rows[i].running_balance_minor - rows[i].amount_minor != balance,
                    i not in follows,  # balances can repeat (purchase, refund)
                    i,
                ),
            )
            ready.discard(pick)
            placed.add(pick)
            out.append(rows[pick])
            balance = rows[pick].running_balance_minor
            follows = after[pick]
            for nxt in after[pick]:
                preds[nxt] -= 1
                if preds[nxt] == 0 and nxt not in placed:  # a cycle break placed it
                    ready.add(nxt)
        return out

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
