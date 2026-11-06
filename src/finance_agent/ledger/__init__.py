"""The Ledger application interface: the one in-process seam used by the CLI,
the API and the tests. Takes a connection from `storage.open_db` and a user_id;
never returns floats."""

import hashlib
import logging
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Select, case, func, select, union

from finance_agent import ingest
from finance_agent.ingest import simplefin
from finance_agent.storage import Scoped, keyed_ref, schema

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
    # rows matched to stored rows where another consistent placement also fits,
    # such as one row that is either of two twins (ADR 0006); longest, then
    # earliest, is used
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


def _overlaps(new: list[Identity], run: list[Identity]) -> list[tuple[int, int]]:
    """Every (length, offset) at which `new` overlaps `run` as one contiguous,
    consistent, non-empty block (new[i] is run[i + offset]), earliest first."""
    out: list[tuple[int, int]] = []
    for o in range(1 - len(new), len(run)):
        lo, hi = max(0, -o), min(len(new), len(run) - o)
        if all(new[i] == run[i + o] for i in range(lo, hi)):
            out.append((hi - lo, o))
    return out


@dataclass(frozen=True)
class Categorisation:
    kind: str  # Spending, Income, Transfer, Refund or Fee
    merchant: str | None
    category: tuple[str, str] | None  # (top level, subcategory)
    source: str  # "correction", "merchant:<name>", "rule:<id>" or "default"
    taxonomy_version: int
    merchant_id: int | None = None


@dataclass(frozen=True)
class Correction:
    """A user's Category for one Transaction or for every Transaction of a
    Merchant (ADR 0001). The Transaction itself never changes."""

    id: int
    transaction_id: int | None
    merchant_id: int | None
    category: tuple[str, str]
    created_at: datetime
    reverted_at: datetime | None


class UnknownTarget(ValueError):
    """No such Transaction, Merchant, Category or Correction for this user."""


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
    running_balance: Decimal | None  # None for synced rows (no running balance)
    statement_imports: frozenset[str]  # file hashes of every import it appeared in
    id: int = 0
    bank_type: str = ""  # the Bank's Transaction Type code; not the Kind
    transfer_with: int | None = None  # the other leg of an own-account Transfer
    categorisation: Categorisation | None = None  # None until `categorise.run`


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
        added = known = ambiguous = 0
        stored = False
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
            stored = True
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
            for position, row in enumerate(parsed.rows):
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
        for account_id in accounts:
            self._match(account_id)  # ADR 0014: after any Statement Import
        if added:
            self._link_transfers()
        if stored:
            self._db.bump_ledger_version()
        self._recompute_gaps()
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
                None
                if r.running_balance_minor is None
                else _decimal(r.running_balance_minor),
                frozenset(hashes.get(r.id, ())),
                r.id,
                r.bank_type,
                other_leg.get(r.id),
                cats.get(r.id),
            )
            for r in rows
        ]

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
        self._db.bump_ledger_version()  # a stored Statement Import, as in import_files
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
        self._link_transfers()  # synced rows are Transfer legs too
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
        self._link_transfers()

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
        synced key and sources; the synced copy goes. Its Categorisation and
        Transfer link are derived, so they go too and are rebuilt."""
        t, tl, cat = schema.transaction, schema.transfer_link, schema.categorisation
        self._db.execute(self._db.delete(cat).where(cat.c.transaction_id == synced_id))
        self._db.execute(
            self._db.delete(tl).where(
                (tl.c.from_transaction_id == synced_id)
                | (tl.c.to_transaction_id == synced_id)
            )
        )
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
        by_txn: dict[int, tuple[str, str]] = {}
        by_merchant: dict[int, tuple[str, str]] = {}
        for k in self.corrections():  # oldest first: the latest active one wins
            if k.reverted_at is None and k.transaction_id is not None:
                by_txn[k.transaction_id] = k.category
            elif k.reverted_at is None and k.merchant_id is not None:
                by_merchant[k.merchant_id] = k.category
        out: dict[int, Categorisation] = {}
        for r in rows:
            category = (r.top, r.sub) if r.sub is not None else None
            source = r.source
            # Corrections beat every rule (ADR 0005); Kinds without a Category
            # (Income, Transfer, Fee) ignore them
            fix = by_txn.get(r.transaction_id) or by_merchant.get(r.merchant_id)
            if fix and r.kind in ("Spending", "Refund"):
                category, source = fix, "correction"
            out[r.transaction_id] = Categorisation(
                r.kind, r.merchant, category, source, r.taxonomy_version, r.merchant_id
            )
        return out

    def ledger_version(self) -> int:
        """Bumped by every import that stores a file and every Correction."""
        return self._db.ledger_version()

    def correct_transaction(
        self, transaction_id: int, category: tuple[str, str]
    ) -> Correction:
        """Give one Transaction this Category, over any rule or Merchant."""
        t = schema.transaction
        self._exists(self._db.select(t, t.c.id).where(t.c.id == transaction_id))
        return self._correct(category, transaction_id=transaction_id)

    def correct_merchant(
        self, merchant_id: int, category: tuple[str, str]
    ) -> Correction:
        """Give every Transaction of this Merchant (now and future) this Category;
        a Correction for one Transaction still beats it."""
        m = schema.merchant
        self._exists(self._db.select(m, m.c.id).where(m.c.id == merchant_id))
        return self._correct(category, merchant_id=merchant_id)

    def revert_correction(self, correction_id: int) -> None:
        """Undo a Correction; it stays in the history, marked reverted."""
        k = schema.correction
        res = self._db.execute(
            self._db.update(k)
            .where(k.c.id == correction_id, k.c.reverted_at.is_(None))
            .values(reverted_at=datetime.now(UTC))
        )
        if res.rowcount != 1:
            raise UnknownTarget("no such active Correction")
        self._db.bump_ledger_version()

    def corrections(self) -> list[Correction]:
        """Every Correction, reverted ones included, oldest first."""
        k, sub, top = (
            schema.correction,
            schema.category.alias("sub"),
            schema.category.alias("top"),
        )
        rows = self._db.execute(
            self._db.select(
                k,
                k.c.id,
                k.c.transaction_id,
                k.c.merchant_id,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
                k.c.created_at,
                k.c.reverted_at,
            )
            .join(sub, sub.c.id == k.c.category_id)
            .join(top, top.c.id == sub.c.parent_id)
            .order_by(k.c.id)
        )
        return [
            Correction(
                r.id,
                r.transaction_id,
                r.merchant_id,
                (r.top, r.sub),
                r.created_at,
                r.reverted_at,
            )
            for r in rows
        ]

    def _correct(self, category: tuple[str, str], **target: int) -> Correction:
        sub, top = schema.category, schema.category.alias("top")
        latest = self._db.execute(
            self._db.select(sub, func.max(sub.c.taxonomy_version))
        ).scalar()
        category_id = self._exists(  # a leaf of the current taxonomy version only
            self._db.select(sub, sub.c.id)
            .join(top, top.c.id == sub.c.parent_id)
            .where(
                top.c.name == category[0],
                sub.c.name == category[1],
                sub.c.taxonomy_version == latest,
            )
        )
        cid = self._db.insert(
            schema.correction,
            category_id=category_id,
            created_at=datetime.now(UTC),
            **target,
        )
        self._db.bump_ledger_version()
        return next(k for k in self.corrections() if k.id == cid)

    def _exists(self, stmt: Select[Any]) -> int:
        row = self._db.execute(stmt).first()
        if row is None:
            raise UnknownTarget("unknown target")
        return int(row[0])

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

    def _align(
        self, account_id: int, rows: list[ingest.ParsedRow]
    ) -> tuple[dict[int, int], int]:
        """ADR 0006: place a new file's rows (one Account, Bank order) against the
        stored runs. A file is a contiguous window of the Bank's history, so its
        overlap with each run is one contiguous block; the longest consistent
        block wins. Returns ({row line: Transaction id}, rows placed ambiguously)."""
        new = [row_identity(r) for r in rows]
        runs, identity = self._bank_order_runs(account_id)
        found = [
            (length, n, o, range(max(0, -o), max(0, -o) + length))
            for n, run in enumerate(runs)
            for length, o in _overlaps(new, [identity[t] for t in run])
        ]
        # a row, or stored Transaction, that another consistent placement also
        # covers may be the other twin; with P R P R P even a unique longest
        # overlap is unsure (ADR 0006)
        cover = Counter(i for *_, span in found for i in span)
        cover_t = Counter(runs[n][i + o] for _, n, o, span in found for i in span)
        placed: dict[int, int] = {}
        used: set[int] = set()  # a file overlaps each run in one block at most
        for _, n, o, span in sorted(found, key=lambda f: -f[0]):  # stable
            if n in used or any(i in placed for i in span):
                continue  # a longer or earlier block already placed these rows
            used.add(n)
            placed |= {i: runs[n][i + o] for i in span}
        unsure = sum(cover[i] > 1 or cover_t[t] > 1 for i, t in placed.items())
        return {rows[i].line: t for i, t in placed.items()}, unsure

    def latest_month(self) -> str | None:
        """The month (YYYY-MM) of the newest Transaction, or None if empty."""
        t = schema.transaction
        newest = self._db.execute(self._db.select(t, func.max(t.c.posted_on))).scalar()
        return None if newest is None else f"{newest:%Y-%m}"

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
                ).where(
                    t.c.account_id == account_id,
                    # shortcut: synced rows have no running balance, so Bank order
                    # and these balances cover CSV rows only; synced Accounts
                    # reconcile through sync_gaps() (ADR 0014). Unify when the
                    # Accounts screen shows synced balances.
                    t.c.running_balance_minor.is_not(None),
                )
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
            .order_by(src.c.statement_import_id, src.c.bank_position)
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
