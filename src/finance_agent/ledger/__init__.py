"""The Ledger application interface: the one in-process seam used by the CLI,
the API and the tests. Takes a connection from `storage.open_db` and a user_id;
never returns floats."""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, case, func

from finance_agent import ingest
from finance_agent.storage import Scoped, keyed_ref, schema


@dataclass(frozen=True)
class Rejection:
    file: str
    line: int
    reason: str


@dataclass(frozen=True)
class ImportReport:
    added: int
    already_known: int
    rejected: list[Rejection]
    accounts: list[int]  # internal Account IDs touched


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
        accounts: set[int] = set()
        for path in paths:
            data = path.read_bytes()
            parsed = ingest.parse(data)
            digest = hashlib.sha256(data).hexdigest()
            si = schema.statement_import
            if self._db.execute(
                self._db.select(si, si.c.id).where(si.c.file_hash == digest)
            ).first():
                known += len(parsed.rows)
                continue
            import_id = self._db.insert(
                si,
                source="csv",
                file_hash=digest,
                original_bytes=data,
                imported_at=datetime.now(UTC),
            )
            bank_id = self._bank_id(parsed.bank)
            for row in parsed.rows:
                account_id = self._account_id(bank_id, row)
                accounts.add(account_id)
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
                self._db.insert(
                    schema.transaction_source,
                    transaction_id=txn_id,
                    statement_import_id=import_id,
                    row_number=row.line,
                )
                added += 1
            rejected += [
                Rejection(path.name, r.line, r.reason) for r in parsed.rejected
            ]
        return ImportReport(added, known, rejected, sorted(accounts))

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
        ref = keyed_ref(row.account_ref)
        found = self._db.execute(
            self._db.select(a, a.c.id).where(a.c.external_ref == ref)
        ).scalar()
        if found is not None:
            return int(found)
        new = self._db.insert(
            a,
            bank_id=bank_id,
            external_ref=ref,
            name=f"Account {row.masked_number}",
            kind="current",
            masked_number=row.masked_number,
            currency=row.currency,
        )
        return new
