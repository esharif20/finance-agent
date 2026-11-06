"""The Ledger application interface: the one in-process seam used by the CLI,
the API and the tests. Takes a connection from `storage.open_db` and a user_id;
never returns floats."""

import hashlib
import logging
import time
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, case, func, select

from finance_agent import ingest
from finance_agent.ingest import simplefin
from finance_agent.storage import Scoped, keyed_ref, schema


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
class Transaction:
    posted_on: date
    description: str
    amount: Decimal
    running_balance: Decimal | None  # None for synced rows (no running balance)
    statement_imports: frozenset[str]  # file hashes of every import it appeared in
    id: int = 0


@dataclass(frozen=True)
class SyncGap:
    account: AffectedAccount
    balance_at: datetime  # the provider's balance-date, UTC
    amount: Decimal  # reported balance minus Ledger-implied balance (ADR 0014)


@dataclass(frozen=True)
class SyncReport:
    added: int  # new Transactions, after matching
    matched: int  # synced rows merged into an existing CSV Transaction
    already_known: int
    changed_known: int  # known IDs arriving with other fields; stored rows unchanged
    rejected_accounts: int  # one ID with differing rows: that Account's rows refused
    provider_duplicates: int  # rows in new Possible Duplicates (ADR 0014 (a), (b))
    pending_explained: int  # Gaps equal to the pending sum (still opened)
    provider_errors: int
    gaps: list[SyncGap]
    accounts: list[AffectedAccount]


@dataclass(frozen=True)
class MonthSummary:
    month: str  # YYYY-MM
    currency: str
    money_in: Decimal
    money_out: Decimal  # positive
    net: Decimal


def _decimal(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)


MATCH_DAYS = 3  # ADR 0014; first guess until a superseding ADR


def normalise(description: str) -> str:
    """ADR 0014: NFKC, casefold, keep only Unicode letters and numbers."""
    folded = unicodedata.normalize("NFKC", description).casefold()
    return "".join(c for c in folded if unicodedata.category(c)[0] in "LN")


def _end_of_day_ts(d: date) -> int:
    nxt = datetime.combine(d + timedelta(days=1), clock(0), simplefin.LONDON)
    return int(nxt.timestamp()) - 1


@dataclass(frozen=True)
class _MatchRow:
    id: int
    synced: bool
    posted_on: date
    order: str | int  # provider ID for synced rows, record order for CSV rows


def _components(rows: list[_MatchRow]) -> list[list[_MatchRow]]:
    """Connected components (size > 1) of the CSV-to-synced candidate graph."""
    parent = list(range(len(rows)))

    def find(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    for i, x in enumerate(rows):
        for j, y in enumerate(rows):
            if (
                not x.synced
                and y.synced
                and abs((x.posted_on - y.posted_on).days) <= MATCH_DAYS
            ):
                parent[find(i)] = find(j)
    out: dict[int, list[_MatchRow]] = defaultdict(list)
    for i, x in enumerate(rows):
        out[find(i)].append(x)
    return [c for c in out.values() if len(c) > 1]


def _month_bounds(month: str) -> tuple[date, date]:
    start = datetime.strptime(month, "%Y-%m").date()
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


class Ledger:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)
        self._merged = 0  # pairs merged by _match, for reports

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
        for account_id in accounts:
            self._match(account_id)  # ADR 0014: after any Statement Import
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
                None
                if r.running_balance_minor is None
                else _decimal(r.running_balance_minor),
                frozenset(hashes.get(r.id, ())),
                r.id,
            )
            for r in rows
        ]

    def accounts(self) -> list[AffectedAccount]:
        a = schema.account
        rows = self._db.execute(
            self._db.select(a, a.c.id, a.c.masked_number).order_by(a.c.id)
        )
        return [AffectedAccount(r.id, r.masked_number) for r in rows]

    # --- SimpleFIN sync (ADR 0014) -------------------------------------------

    def apply_sync(self, raw: bytes) -> SyncReport:
        """Apply one SimpleFIN `/accounts` response as one Statement Import.
        Raises ingest's UnreadableResponse (a ValueError) and stores nothing
        if the response breaks the protocol."""
        started = time.perf_counter_ns()
        parsed = simplefin.parse(raw)
        digest = hashlib.sha256(raw).hexdigest()
        si = schema.statement_import
        if self._db.execute(
            self._db.select(si, si.c.id).where(si.c.file_hash == digest)
        ).first():
            n = sum(len(a.rows) for a in parsed.accounts)
            return SyncReport(0, 0, n, 0, 0, 0, 0, parsed.errors, [], [])
        import_id = self._db.insert(
            si,
            source="simplefin",
            file_hash=digest,
            original_bytes=raw,  # pending rows live only here (ADR 0014)
            imported_at=datetime.now(UTC),
        )
        added = known = changed = rejected = dupes = pending_explained = 0
        self._merged = 0
        gaps: list[SyncGap] = []
        touched: dict[int, str] = {}
        position = 0
        for acc in parsed.accounts:
            if acc.conflicting_ids:
                rejected += 1
                continue
            account_id, masked = self._synced_account(acc)
            new_ids = {
                row.provider_id
                for row in acc.rows
                if self._synced_key(account_id, row.provider_id) is None
            }
            for row in acc.rows:
                position += 1
                found = self._synced_key(account_id, row.provider_id)
                if found is None:
                    txn_id = self._db.insert(
                        schema.transaction,
                        account_id=account_id,
                        posted_on=row.posted_on,
                        bank_type="",
                        description=row.description,
                        amount_minor=row.amount_minor,
                        currency=acc.currency,
                        running_balance_minor=None,
                        status="completed",
                        origin="simplefin",
                    )
                    self._db.insert(
                        schema.synced_key,
                        account_id=account_id,
                        provider_id=row.provider_id,
                        transaction_id=txn_id,
                        posted_ts=row.posted_ts,
                        transacted_ts=row.transacted_ts,
                        amount_minor=row.amount_minor,
                        description=row.description,
                    )
                    added += 1
                    touched[account_id] = masked
                else:
                    txn_id = int(found.transaction_id)
                    known += 1
                    if (found.amount_minor, found.description, found.posted_ts) != (
                        row.amount_minor,
                        row.description,
                        row.posted_ts,
                    ):
                        changed += 1  # never rewrites the stored Transaction
                self._db.insert(
                    schema.transaction_source,
                    transaction_id=txn_id,
                    statement_import_id=import_id,
                    row_number=position,
                )
            self._match(account_id)
            dupes += self._record_duplicates(account_id, import_id, acc, new_ids)
            gap = self._reconcile(account_id, masked, acc)
            if gap is not None and gap.amount == _decimal(acc.pending_minor):
                pending_explained += 1  # a hint only: ADR 0014 opens every Gap
            if gap is not None:
                self._db.insert(
                    schema.sync_gap,
                    account_id=account_id,
                    statement_import_id=import_id,
                    balance_ts=acc.balance_ts,
                    amount_minor=int(gap.amount.scaleb(2)),
                )
                gaps.append(gap)
        log.info(
            "sync statement_import_id=%d added=%d known=%d changed=%d rejected=%d "
            "duplicates=%d gaps=%d ms=%d",
            import_id,
            added,
            known,
            changed,
            rejected,
            dupes,
            len(gaps),
            (time.perf_counter_ns() - started) // 1_000_000,
        )
        return SyncReport(
            added - self._merged,
            self._merged,
            known,
            changed,
            rejected,
            dupes,
            pending_explained,
            parsed.errors,
            gaps,
            [AffectedAccount(i, m) for i, m in sorted(touched.items())],
        )

    def link_synced_account(self, provider_account_id: str, account_id: int) -> None:
        """Link a SimpleFIN Account to an existing (CSV) Account, once. If the
        synced Account already has rows, they move into it, then rows match."""
        a = schema.account
        ref = keyed_ref("simplefin:" + provider_account_id)
        target = self._db.execute(
            self._db.select(a, a.c.provider_ref, a.c.currency).where(
                a.c.id == account_id
            )
        ).first()
        if target is None or target.provider_ref is not None:
            raise ValueError("that Account does not exist or is already linked")
        old = self._db.execute(
            self._db.select(a, a.c.id, a.c.currency).where(a.c.provider_ref == ref)
        ).first()
        if old is not None:
            if old.currency != target.currency:
                raise ValueError("the Accounts have different currencies")
            for table in (
                schema.transaction,
                schema.synced_key,
                schema.sync_anchor,
                schema.sync_gap,
                schema.possible_duplicate,
            ):
                self._db.execute(
                    self._db.update(table)
                    .where(table.c.account_id == old.id)
                    .values(account_id=account_id)
                )
            self._db.execute(self._db.delete(a).where(a.c.id == old.id))
        self._db.execute(
            self._db.update(a).where(a.c.id == account_id).values(provider_ref=ref)
        )
        self._match(account_id)

    def ambiguous_matches(self) -> list[frozenset[int]]:
        """ADR 0014 ambiguous CSV-to-sync components, re-evaluated each import."""
        a = schema.account
        out: list[frozenset[int]] = []
        for (account_id,) in self._db.execute(
            self._db.select(a, a.c.id).where(a.c.provider_ref.is_not(None))
        ):
            out += self._match(account_id, merge=False)
        return out

    def possible_duplicates(self) -> list[frozenset[int]]:
        """ADR 0014 Possible Duplicates: ambiguous matching components, then
        provider duplicates (a) and re-issued IDs (b). Kept apart, surfaced."""
        out = self.ambiguous_matches()
        pd = schema.possible_duplicate
        groups: dict[int, set[int]] = defaultdict(set)
        for r in self._db.execute(
            self._db.select(pd, pd.c.group_id, pd.c.transaction_id)
        ):
            groups[int(r.group_id)].add(int(r.transaction_id))
        return out + [frozenset(g) for _, g in sorted(groups.items())]

    def sync_gaps(self) -> list[SyncGap]:
        g, a = schema.sync_gap, schema.account
        rows = self._db.execute(
            self._db.select(g, g.c.account_id, a.c.masked_number, g.c.balance_ts)
            .add_columns(g.c.amount_minor)
            .join(a, a.c.id == g.c.account_id)
            .order_by(g.c.id)
        )
        return [
            SyncGap(
                AffectedAccount(r.account_id, r.masked_number),
                datetime.fromtimestamp(r.balance_ts, UTC),
                _decimal(r.amount_minor),
            )
            for r in rows
        ]

    def sync_since(self, today: date) -> date:
        """First day to fetch: the last synced posted date minus an overlap, or
        the provider's history on a first sync."""
        k = schema.synced_key
        newest = (
            self._db.select(k, func.max(k.c.posted_ts).label("ts"))
            .group_by(k.c.account_id)
            .subquery()
        )
        # the stalest Account decides, so no Account's late rows fall outside
        last = self._db.execute(select(func.min(newest.c.ts))).scalar()
        if last is None:
            return today - timedelta(days=simplefin.FIRST_SYNC_DAYS)
        posted = datetime.fromtimestamp(int(last), simplefin.LONDON).date()
        return min(today, posted) - timedelta(days=simplefin.OVERLAP_DAYS)

    def _synced_account(self, acc: simplefin.SyncedAccount) -> tuple[int, str]:
        a = schema.account
        ref = keyed_ref("simplefin:" + acc.provider_id)
        found = self._db.execute(
            self._db.select(a, a.c.id, a.c.masked_number).where(a.c.provider_ref == ref)
        ).first()
        if found is not None:
            return int(found.id), str(found.masked_number)
        masked = "••" + acc.provider_id[-4:]  # opaque provider ID, not a number
        new = self._db.insert(
            a,
            bank_id=self._bank_id(acc.org[:64] or "SimpleFIN"),
            external_ref=ref,
            provider_ref=ref,
            name=acc.name[:128] or "Synced account",
            kind="current",
            masked_number=masked,
            currency=acc.currency,
        )
        return new, masked

    def _synced_key(self, account_id: int, provider_id: str) -> Any:
        k = schema.synced_key
        return self._db.execute(
            self._db.select(
                k, k.c.transaction_id, k.c.amount_minor, k.c.description, k.c.posted_ts
            ).where(k.c.account_id == account_id, k.c.provider_id == provider_id)
        ).first()

    def _record_duplicates(
        self,
        account_id: int,
        import_id: int,
        acc: simplefin.SyncedAccount,
        new: set[str],
    ) -> int:
        """ADR 0014 Possible Duplicates for rows new in this response, kept and
        surfaced, never merged: (a) same date, amount and raw text under distinct
        IDs in this response; (b) same amount and text within MATCH_DAYS of a
        known row whose ID is absent although this response covers its date
        (a re-issued ID). Call after `_match`, so ids are final. Returns rows."""
        if not new or not acc.rows:
            return 0
        k = schema.synced_key
        txn_of = {
            str(r.provider_id): int(r.transaction_id)
            for r in self._db.execute(
                self._db.select(k, k.c.provider_id, k.c.transaction_id).where(
                    k.c.account_id == account_id
                )
            )
        }
        groups: list[set[int]] = []
        same: dict[tuple[date, int, str], list[str]] = defaultdict(list)
        for row in acc.rows:
            same[(row.posted_on, row.amount_minor, row.description)].append(
                row.provider_id
            )
        groups += [
            {txn_of[i] for i in ids}
            for ids in same.values()
            if len(ids) > 1 and new.intersection(ids)
        ]
        seen = {row.provider_id for row in acc.rows}
        lo = min(row.posted_on for row in acc.rows)
        hi = max(row.posted_on for row in acc.rows)
        absent = [
            r
            for r in self._db.execute(
                self._db.select(k, k.c.provider_id, k.c.transaction_id, k.c.posted_ts)
                .add_columns(k.c.amount_minor, k.c.description)
                .where(k.c.account_id == account_id)
            )
            if r.provider_id not in seen
        ]
        for row in acc.rows:
            if row.provider_id not in new:
                continue
            for r in absent:
                on = datetime.fromtimestamp(int(r.posted_ts), simplefin.LONDON).date()
                if (
                    lo <= on <= hi
                    and (r.amount_minor, r.description)
                    == (row.amount_minor, row.description)
                    and abs((on - row.posted_on).days) <= MATCH_DAYS
                ):
                    groups.append({int(r.transaction_id), txn_of[row.provider_id]})
        pd = schema.possible_duplicate
        rows = 0
        for g in groups:
            if len(g) < 2:
                continue  # both rows matched into one CSV Transaction
            group_id = 1 + int(
                self._db.execute(
                    self._db.select(pd, func.coalesce(func.max(pd.c.group_id), 0))
                ).scalar()
                or 0
            )
            for txn_id in g:
                self._db.insert(
                    pd,
                    group_id=group_id,
                    transaction_id=txn_id,
                    account_id=account_id,
                    statement_import_id=import_id,
                )
            rows += len(g)
        return rows

    def _match(self, account_id: int, *, merge: bool = True) -> list[frozenset[int]]:
        """ADR 0014 CSV-to-sync matching over the Account's unmatched rows.
        Pairs merge into the CSV Transaction; returns ambiguous components."""
        a, t, k = schema.account, schema.transaction, schema.synced_key
        linked = self._db.execute(
            self._db.select(a, a.c.provider_ref).where(a.c.id == account_id)
        ).scalar()
        if linked is None:
            return []
        rows = self._db.execute(
            self._db.select(
                t,
                t.c.id,
                t.c.origin,
                t.c.posted_on,
                t.c.description,
                t.c.amount_minor,
                t.c.currency,
                k.c.provider_id,
            )
            .outerjoin(k, k.c.transaction_id == t.c.id)
            .where(t.c.account_id == account_id)
        ).all()
        groups: dict[tuple[int, str, str], list[_MatchRow]] = defaultdict(list)
        for r in rows:
            synced = r.origin == "simplefin"
            if not synced and r.provider_id is not None:
                continue  # a CSV row already matched: final
            text = normalise(r.description)
            if text:
                groups[(r.amount_minor, r.currency, text)].append(
                    _MatchRow(r.id, synced, r.posted_on, r.provider_id or r.id)
                )
        ambiguous: list[frozenset[int]] = []
        for group in groups.values():
            for component in _components(group):
                csv = sorted(
                    (m for m in component if not m.synced),
                    key=lambda m: (m.posted_on, m.order),
                )
                syn = sorted(
                    (m for m in component if m.synced),
                    key=lambda m: (m.posted_on, m.order),
                )
                pairs = list(zip(csv, syn, strict=False))
                if len(csv) == len(syn) and all(
                    abs((c.posted_on - s.posted_on).days) <= MATCH_DAYS
                    for c, s in pairs
                ):
                    if merge:
                        for c, s in pairs:
                            self._merge(c.id, s.id)
                else:
                    ambiguous.append(frozenset(m.id for m in component))
        return ambiguous

    def _merge(self, csv_id: int, synced_id: int) -> None:
        """A matched pair becomes one Transaction: the CSV one stays and takes the
        synced key and sources; the just-synced copy goes (it has no Corrections)."""
        t = schema.transaction
        for table in (
            schema.synced_key,
            schema.transaction_source,
            schema.possible_duplicate,
        ):
            self._db.execute(
                self._db.update(table)
                .where(table.c.transaction_id == synced_id)
                .values(transaction_id=csv_id)
            )
        self._db.execute(self._db.delete(t).where(t.c.id == synced_id))
        self._merged += 1

    def _reconcile(
        self,
        account_id: int,
        masked: str,
        acc: simplefin.SyncedAccount,
    ) -> SyncGap | None:
        """ADR 0014: B_k - B_1 - sum of Transactions with instant in (t_1, t_k]."""
        anchor = schema.sync_anchor
        first = self._db.execute(
            self._db.select(anchor, anchor.c.balance_minor, anchor.c.balance_ts).where(
                anchor.c.account_id == account_id
            )
        ).first()
        if first is None:
            self._db.insert(
                anchor,
                account_id=account_id,
                balance_minor=acc.balance_minor,
                balance_ts=acc.balance_ts,
            )
            return None
        t, k = schema.transaction, schema.synced_key
        moved = 0
        for r in self._db.execute(
            self._db.select(t, t.c.posted_on, t.c.amount_minor, k.c.posted_ts)
            .outerjoin(k, k.c.transaction_id == t.c.id)
            .where(t.c.account_id == account_id)
        ):
            instant = (
                r.posted_ts if r.posted_ts is not None else _end_of_day_ts(r.posted_on)
            )
            if first.balance_ts < instant <= acc.balance_ts:
                moved += r.amount_minor
        gap = acc.balance_minor - first.balance_minor - moved
        if not gap:
            return None
        return SyncGap(
            AffectedAccount(account_id, masked),
            datetime.fromtimestamp(acc.balance_ts, UTC),
            _decimal(gap),
        )

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
