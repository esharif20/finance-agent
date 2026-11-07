"""The Ledger application interface: the one in-process seam used by the CLI,
the API and the tests. Takes a connection from `storage.open_db` and a user_id;
never returns floats."""

import hashlib
import logging
import time
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Connection,
    Select,
    func,
    or_,
    select,
    union,
)

from finance_agent import ingest
from finance_agent.ingest import simplefin
from finance_agent.ledger import fx
from finance_agent.ledger.taxonomy import TAXONOMY, current
from finance_agent.ledger.taxonomy import UNCATEGORISED as UNCATEGORISED
from finance_agent.storage import Scoped, keyed_ref, schema

TRANSFER_TYPES = ("TFR",)  # Lloyds Type that favours a pair; not required
TRANSFER_DAYS = 3  # the two legs post at most this many days apart
EXCHANGE = "EXCHANGE"  # Revolut Type of each leg of a currency exchange (#156)


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
    files_imported: int = 0  # files stored or already known; not rejected
    skipped: int = 0  # Revolut PENDING or REVERTED rows left out; a count only


log = logging.getLogger(__name__)  # counts, IDs and timings only

Identity = tuple[date, str, int, int | None]  # None: the Bank gives no balance


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
    # "correction", "split", "split_refund", "merchant:<name>", "rule:<id>" or
    # "default"
    source: str
    taxonomy_version: int
    merchant_id: int | None = None
    # a split Transaction's parts as totals count them, adding up to its amount
    # (one whole part when the Split needs re-checking); `category` is then
    # the largest part's
    parts: tuple["SplitPart", ...] = ()
    # a Refund counted against this split Transaction's largest part until
    # the user says which part it was for
    refund_of: int | None = None


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


SPLIT_PARTS = range(2, 11)  # a Split has 2-10 parts


@dataclass(frozen=True)
class SplitPart:
    amount_minor: int  # signed as the Transaction: spending negative
    category: tuple[str, str]


@dataclass(frozen=True)
class Split:
    """A Split Correction (#123): one Transaction divided into parts, each in
    its own Category. The parts added up to the Transaction when made; if the
    amount has changed since, `needs_recheck` is set and they are never
    rescaled (the whole amount counts under the largest part's Category)."""

    id: int  # the Correction's id
    transaction_id: int
    parts: tuple[SplitPart, ...]
    created_at: datetime
    reverted_at: datetime | None
    amount_minor: int  # the Transaction's amount now
    needs_recheck: bool = False
    currency: str = "GBP"  # the Transaction's; parts are in it too

    @property
    def largest(self) -> SplitPart:
        """The largest part by size; the first one on a tie."""
        return max(self.parts, key=lambda p: abs(p.amount_minor))


NOTE_MAX = 140  # characters (code points)
# besides Cc and Cs: bidi overrides and isolates, and line/paragraph separators
_NOTE_BANNED = set("\u2028\u2029\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


@dataclass(frozen=True)
class Note:
    """One version of a purpose note (#124) on a Transaction, or on part `part`
    of its Split. The live version has no `superseded_at`."""

    id: int
    transaction_id: int
    part: int | None
    text: str
    created_at: datetime
    superseded_at: datetime | None


class InvalidNote(ValueError):
    """Empty, longer than NOTE_MAX, or holding control characters. The message
    never quotes the text."""


def _check_note(text: str) -> None:
    if not text.strip():
        raise InvalidNote("a note cannot be empty")
    if len(text) > NOTE_MAX:
        raise InvalidNote(f"a note is at most {NOTE_MAX} characters")
    if any(c in _NOTE_BANNED or unicodedata.category(c) in ("Cc", "Cs") for c in text):
        raise InvalidNote("a note cannot hold control characters")


class InvalidSplit(ValueError):
    """Parts that are not 2-10 non-zero integers of the Transaction's sign
    adding up to it exactly, or a Transaction that cannot be split."""


def split_parts(total_minor: int, amounts: Sequence[int | None]) -> list[int]:
    """Every part's amount in minor units, the last one as the remainder when
    it is None. Plain arithmetic (ADR 0007); refuses anything that is not an
    exact split of `total_minor`."""
    if len(amounts) not in SPLIT_PARTS:
        raise InvalidSplit("a Split has 2 to 10 parts")
    *typed, last = amounts
    if any(type(a) is not int for a in typed) or type(last) not in (int, type(None)):
        raise InvalidSplit("only the last part may be left for the remainder")
    out: list[int] = [a for a in typed if type(a) is int]
    out.append(total_minor - sum(out) if last is None else last)
    if total_minor == 0 or any(a == 0 for a in out):
        raise InvalidSplit("no part may be zero")
    if any((a < 0) != (total_minor < 0) for a in out):
        raise InvalidSplit("every part takes the Transaction's sign")
    if sum(out) != total_minor:
        raise InvalidSplit("the parts must add up to the Transaction exactly")
    return out


# Subscription registry (#103). kind -> the top-level Category its groups come from
REGISTRY_KINDS = {"subscription": "Subscriptions", "bill": "Bills"}
DETECTED_REASONS = ("known_service", "pattern")  # user_confirmed, user_added: user only
DETECTED_STATUSES = ("suggested", "active")  # suggested: ask "Is this a subscription?"
INTERVALS = ("weekly", "monthly", "annual")
NOT_SUBSCRIPTION = "not_subscription"  # a negative entry
USER_CANCELLED = "user_cancelled"  # the user cancelled it; a further charge is a flag


@dataclass(frozen=True)
class SubscriptionEntry:
    """A Merchant's place in the registry: why it is (or is not) a Subscription
    or Bill, and who said so last."""

    merchant_id: int
    merchant: str
    kind: str | None  # "subscription" or "bill"; None for a negative entry
    group: str | None
    status: str  # suggested, active, user_cancelled or not_subscription
    reason: str | None  # known_service, user_confirmed, pattern or user_added
    source: str  # "user" or "detection": who made the latest change
    evidence_on: date
    charge_count: int | None
    interval: str | None
    created_at: datetime  # when the Merchant first entered the registry
    changed_at: datetime
    # when `status` began: a regroup moves changed_at but not this (#190)
    status_since: datetime | None = None


class InvalidEntry(ValueError):
    """A kind and group not in the taxonomy, or a reason or status not allowed."""


CATEGORISED_KINDS = ("Spending", "Refund")  # the only Kinds that take a Category


class KindHasNoCategory(ValueError):
    """Transfers, Income and Fees take no Category, so a Correction is refused."""


# "Always ask" (#125): the bucket a flagged Merchant's unanswered payments count
# in. Not a Category: nothing can be corrected to it.
TO_REVIEW = "To review"
ALWAYS_ASK_SOURCES = ("user", "suggestion")  # how the user set the flag
SUGGEST_AFTER = 2  # distinct corrected Categories before "Always ask" is offered


class AlwaysAskMerchant(ValueError):
    """An "Always ask" Merchant takes no Merchant-wide Correction."""


@dataclass(frozen=True)
class Merchant:
    id: int  # stable across imports and rule changes
    name: str
    default_category: tuple[str, str] | None
    always_ask: str | None = None  # its source when flagged, else None


@dataclass(frozen=True)
class Transaction:
    posted_on: date
    description: str
    amount: Decimal
    running_balance: Decimal | None  # None for synced and HSBC rows (none given)
    statement_imports: frozenset[str]  # file hashes of every import it appeared in
    id: int = 0
    bank_type: str = ""  # the Bank's Transaction Type code; not the Kind
    transfer_with: int | None = None  # the other leg of an own-account Transfer
    categorisation: Categorisation | None = None  # None until `categorise.run`
    account_id: int = 0
    currency: str = "GBP"  # `amount` is in this currency, never converted


@dataclass(frozen=True)
class TransactionPage:
    items: list[Transaction]
    total: int  # every match, before limit and offset


@dataclass(frozen=True)
class SyncGap:
    account: AffectedAccount
    balance_at: datetime  # the provider's balance-date, UTC
    amount: Decimal  # reported balance minus Ledger-implied balance (ADR 0014)


@dataclass(frozen=True)
class SyncStatus:
    """Freshness of the feed. Failures carry an error class and a count only."""

    last_sync_at: datetime | None  # UTC
    last_failure_at: datetime | None  # UTC; cleared by the next success
    failure_class: str | None
    failures_in_a_row: int
    consent_renewed_on: date | None
    consent_expires_on: date | None
    consent_days_left: int | None  # negative once expired
    consent_warning: bool  # inside the warning window, or expired


@dataclass(frozen=True)
class SyncReport:
    added: int  # new Transactions, after matching
    matched: int  # synced rows merged into an existing CSV Transaction
    already_known: int
    changed_known: int  # known IDs arriving with other fields; stored rows unchanged
    rejected_accounts: int  # one ID with differing rows: that Account's rows refused
    provider_duplicates: int  # rows in new Possible Duplicates (ADR 0014 (a), (b))
    provider_errors: int
    gaps: list[SyncGap]
    accounts: list[AffectedAccount]
    skipped_accounts: int = 0  # unsupported currency (#186); no fields kept


@dataclass(frozen=True)
class Spend:
    """Spending minus Refunds in one Category, Subcategory or Merchant;
    negative when Refunds received exceed spending (ADR 0004)."""

    name: str
    amount: Decimal
    children: tuple["Spend", ...] = ()
    # set on Merchant rows (None for "Unknown"); a key, not a figure, so equality
    # stays about names and amounts
    merchant_id: int | None = field(default=None, compare=False)


def _top_merchants(subs: Iterable[Spend], n: int) -> tuple[Spend, ...]:
    """The `n` largest Merchants across Subcategories, keyed by Merchant id."""
    total: dict[tuple[int | None, str], Decimal] = {}
    for m in (m for sub in subs for m in sub.children):
        key = (m.merchant_id, m.name)
        total[key] = total.get(key, Decimal(0)) + m.amount
    ranked = sorted(total.items(), key=lambda kv: (-kv[1], kv[0][1]))[:n]
    return tuple(Spend(name, a, merchant_id=i) for (i, name), a in ranked)


@dataclass(frozen=True)
class CategoryDetail:
    """One Category in one month: its Subcategories, top Merchants (across
    Subcategories), spending per month for the trailing `trend` months (oldest
    first, ending at `month`) and the month's Spending and Refund Transactions."""

    name: str
    total: Decimal
    subcategories: tuple[Spend, ...]
    merchants: tuple[Spend, ...]
    trend: tuple[tuple[str, Decimal], ...]
    transactions: tuple[Transaction, ...]


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
    # the Bank's last running balance (the truth); None when the Bank's export
    # has no balance (HSBC): nothing to reconcile, so no figure and no Gaps
    bank_balance: Decimal | None
    ledger_balance: Decimal | None  # opening balance plus every Transaction we hold
    gaps: list[Gap]  # bank_balance - ledger_balance == sum of these amounts
    bank: str = ""  # the Bank's name, e.g. "Lloyds"
    last_import: datetime | None = None  # newest statement holding its rows
    source: str = ""  # how that statement came in: "csv"


@dataclass(frozen=True)
class Breakdown:
    """A month's spending by Category and what separates it from Money out:
    money_out == spending_after_refunds + refunds + fees (ADR 0004)."""

    categories: tuple[
        Spend, ...
    ]  # Uncategorised and To review are gross; never negative
    unmatched_refunds: Decimal  # <= 0: Refunds with no Category, kept off the ring
    spending_after_refunds: Decimal  # == sum(categories) + unmatched_refunds
    refunds: Decimal  # all Refunds received in the month
    fees: Decimal
    merchants: tuple[Spend, ...] = ()  # top Merchants across Categories


@dataclass(frozen=True)
class MonthSummary:
    month: str  # YYYY-MM
    currency: str
    money_in: Decimal
    money_out: Decimal  # positive
    net: Decimal
    gaps: list[Gap]  # Gaps touching the month: the figures may be incomplete
    # Transfers between the user's own Accounts, left out of money in and out
    transfers_in: Decimal = Decimal(0)
    transfers_out: Decimal = Decimal(0)  # positive
    # foreign-currency Transactions left out for want of a rate (#156)
    rate_needed: int = 0


@dataclass(frozen=True)
class FxMonth:
    """How a month's GBP totals treat its foreign-currency Transactions: each
    one is in `conversions` (with the one rate used) or in `rate_needed`
    (left out of every total, never guessed)."""

    conversions: tuple[fx.Conversion, ...]
    rate_needed: tuple[Transaction, ...]


@dataclass(frozen=True)
class AccountInGbp:
    account_id: int
    conversion: fx.Conversion | None  # None: rate needed


@dataclass(frozen=True)
class BalancesInGbp:
    """Every Account's Bank balance in GBP; `total` is None when any balance is
    unknown (no balance from the Bank, or rate needed)."""

    total: Decimal | None
    accounts: tuple[AccountInGbp, ...]  # foreign-currency Accounts only


@dataclass(frozen=True)
class BankOrderRow:
    id: int
    posted_on: date
    description: str
    amount_minor: int
    running_balance_minor: int


def _shares(c: Categorisation, amount_minor: int) -> list[tuple[tuple[str, str], int]]:
    """(Category, minor units) that a Transaction of `amount_minor` counts as:
    its Split parts when it has a live Split, else all of it in its Category."""
    if c.parts:
        return [(p.category, p.amount_minor) for p in c.parts]
    return [(c.category, amount_minor)] if c.category else []


def _decimal(minor: int) -> Decimal:
    return Decimal(minor).scaleb(-2)


CURRENCY = fx.GBP  # every total is in GBP; foreign figures convert (#156)


def _shares_in_gbp(
    shares: list[tuple[tuple[str, str], int]], amount_minor: int, gbp_minor: int
) -> list[tuple[tuple[str, str], int]]:
    """`shares` of a Transaction of `amount_minor` rescaled to its GBP figure:
    each by share, the last takes the remainder, so they add up exactly."""
    if gbp_minor == amount_minor or not shares:
        return shares
    out: list[tuple[tuple[str, str], int]] = []
    left = gbp_minor
    for i, (cat, minor) in enumerate(shares):
        g = left
        if i < len(shares) - 1:
            exact = Decimal(minor) * gbp_minor / amount_minor
            g = int(exact.to_integral_value(ROUND_HALF_EVEN))
        out.append((cat, g))
        left -= g
    return out


def _only(account_id: int | None) -> list[ColumnElement[bool]]:
    """WHERE terms narrowing a Transaction query to one Account, if given."""
    return [] if account_id is None else [schema.transaction.c.account_id == account_id]


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


def _within(complete: list[tuple[date, date]], d: date) -> bool:
    """[d - MATCH_DAYS, d + MATCH_DAYS] lies inside one run of complete days, so
    the other source must hold this row's counterpart (ADR 0014 amendment)."""
    lo, hi = d - timedelta(MATCH_DAYS), d + timedelta(MATCH_DAYS)
    start = end = date.min
    for a, b in sorted(complete):
        if end != date.min and a <= end + timedelta(1):
            end = max(end, b)
        else:
            start, end = a, b
        if start <= lo and hi <= end:
            return True
    return False


def _pair(rows: list[_MatchRow], must: set[int]) -> list[tuple[int, int]] | None:
    """ADR 0014 amendment: pairs for one component, or None when it is
    ambiguous. A `must` row lies where the other source is complete, so it has
    a counterpart; any other row may or may not. The component resolves only if
    some matching pairs every `must` row and every such matching has the same
    size and pairs the same rows (a smaller one would mean a purchase silently
    lost; a different leftover row could strand a later import's row)."""
    csv = sorted(
        (m for m in rows if not m.synced), key=lambda m: (m.posted_on, m.order)
    )
    syn = sorted((m for m in rows if m.synced), key=lambda m: (m.posted_on, m.order))
    adj = {
        c.id: [s.id for s in syn if abs((c.posted_on - s.posted_on).days) <= MATCH_DAYS]
        for c in csv
    }
    back: dict[int, list[int]] = defaultdict(list)
    for c, ss in adj.items():
        for s in ss:
            back[s].append(c)
    of_c: dict[int, int] = {}
    of_s: dict[int, int] = {}

    def augment(c: int, seen: set[int]) -> bool:
        for s in adj[c]:
            if s not in seen:
                seen.add(s)
                if s not in of_s or augment(of_s[s], seen):
                    of_s[s], of_c[c] = c, s
                    return True
        return False

    # a maximum matching; `must` CSV rows first, so it pairs all it can of them
    for c in sorted(csv, key=lambda m: m.id not in must):
        augment(c.id, set())
    # then swap each unpaired `must` synced row in along an alternating path
    # that frees a synced row nobody requires (CSV rows stay paired)
    for s0 in [s.id for s in syn if s.id in must and s.id not in of_s]:
        prev: dict[int, tuple[int, int]] = {}
        queue, end = [s0], None
        for s in queue:
            if s != s0 and s not in must:
                end = s
                break
            for c in back[s]:
                nxt = of_c[c]  # every neighbour is paired, or the matching grows
                if nxt != s0 and nxt not in prev:
                    prev[nxt] = (c, s)
                    queue.append(nxt)
        if end is None:
            return None
        del of_s[end]
        while end != s0:
            c, end = prev[end]
            of_s[end], of_c[c] = c, end
    if any(c.id in must and c.id not in of_c for c in csv):
        return None
    # a smaller valid matching exists iff an alternating path runs, by paired
    # edges at both ends, from an optional CSV row to an optional synced row
    queue = [of_c[c.id] for c in csv if c.id in of_c and c.id not in must]
    seen = set(queue)
    for s in queue:
        if s not in must:
            return None
        for c in back[s]:
            nxt = of_c.get(c)
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    # an equal-size matching that leaves a different row unpaired is a guess
    # too: that row may be the one a later import's row needed. It exists iff
    # an alternating path runs from an unpaired row to a paired optional row.
    for side, mate, other in ((adj, of_c, of_s), (back, of_s, of_c)):
        queue = [x for x in side if x not in mate]
        seen = set(queue)
        for x in queue:
            for y in side[x]:
                nxt = other[y]  # y is paired, or the matching was not maximum
                if nxt not in must:
                    return None
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
    if len(of_c) == len(csv) == len(syn):  # perfect: date order, as before
        return [(c.id, s.id) for c, s in zip(csv, syn, strict=True)]
    return list(of_c.items())


def _month_bounds(month: str) -> tuple[date, date]:
    start = datetime.strptime(month, "%Y-%m").date()
    end = date(start.year + start.month // 12, start.month % 12 + 1, 1)
    return start, end


class Ledger:
    def __init__(self, conn: Connection, user_id: int) -> None:
        self._db = Scoped(conn, user_id)

    def import_files(self, paths: Sequence[Path]) -> ImportReport:
        added = known = ambiguous = files = skipped = 0
        stored = False
        rejected: list[Rejection] = []
        accounts: dict[int, str] = {}
        for n, path in enumerate(paths, 1):
            started = time.perf_counter_ns()
            data = path.read_bytes()
            try:
                parsed = ingest.parse(data)
            except ingest.UnknownFormat:
                # the whole batch stops (the caller's transaction rolls back), so a
                # file of another Bank is never half of an import
                raise ingest.UnknownFormat(
                    f"{path.name}: no Importer recognises this file's header; "
                    "nothing in this batch was imported"
                ) from None
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
            skipped += parsed.skipped
            digest = hashlib.sha256(data).hexdigest()
            si = schema.statement_import
            if self._db.execute(
                self._db.select(si, si.c.id).where(si.c.file_hash == digest)
            ).first():
                known += len(parsed.rows)
                files += 1
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
                        gbp_minor=row.gbp_minor,
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
            files += 1
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
            files,
            skipped,
        )

    def transactions(self) -> list[Transaction]:
        """Every stored Transaction, oldest first."""
        return self._transactions()

    def transaction(self, transaction_id: int) -> Transaction | None:
        """One Transaction of this user, or None."""
        rows = self._transactions(schema.transaction.c.id == transaction_id)
        return rows[0] if rows else None

    def search_transactions(
        self,
        account_id: int | None = None,
        kind: str | None = None,
        category: str | None = None,
        month: str | None = None,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> TransactionPage:
        """Transactions matching every filter given, newest first. `category` is
        a top-level name, after Corrections; `query` is a case-insensitive
        substring of the description, the Merchant's name or a live note."""
        t, c, m = schema.transaction, schema.categorisation, schema.merchant
        n = schema.note
        terms = _only(account_id)
        if month is not None:
            start, end = _month_bounds(month)
            terms += [t.c.posted_on >= start, t.c.posted_on < end]
        if kind is not None:  # Corrections change the Category, never the Kind
            terms.append(
                t.c.id.in_(
                    self._db.select(c, c.c.transaction_id).where(c.c.kind == kind)
                )
            )
        needle = (query or "").strip().lower()
        if needle:
            named = (
                self._db.select(c, c.c.transaction_id)
                .join(m, self._db.on(m, m.c.id == c.c.merchant_id))
                .where(func.lower(m.c.name).contains(needle, autoescape=True))
            )
            k = schema.correction
            live_splits = self._db.select(k, k.c.id).where(
                k.c.kind == "split", k.c.reverted_at.is_(None)
            )
            noted = self._db.select(n, n.c.transaction_id).where(
                n.c.superseded_at.is_(None),
                or_(n.c.split_id.is_(None), n.c.split_id.in_(live_splits)),
                func.lower(n.c.text).contains(needle, autoescape=True),
            )
            terms.append(
                or_(
                    func.lower(t.c.description).contains(needle, autoescape=True),
                    t.c.id.in_(named),
                    t.c.id.in_(noted),
                )
            )
        rows = [
            x
            for x in reversed(self._transactions(*terms))
            if category is None
            or (
                (k := x.categorisation)
                and k.category
                and any(top == category for (top, _), _ in _shares(k, 0))
            )
        ]
        return TransactionPage(rows[offset : offset + limit], len(rows))

    def _transactions(self, *where: ColumnElement[bool]) -> list[Transaction]:
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
                t.c.account_id,
                t.c.currency,
            )
            .where(*where)
            .order_by(t.c.posted_on, t.c.id)
        ).all()
        other_leg = self._transfer_legs()
        cats = self._categorisations()
        hashes: dict[int, set[str]] = {}
        for link in self._db.execute(
            self._db.select(src, src.c.transaction_id, si.c.file_hash).join(
                si, self._db.on(si, si.c.id == src.c.statement_import_id)
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
                r.account_id,
                r.currency,
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
            return SyncReport(
                added=0,
                matched=0,
                already_known=n,
                changed_known=0,
                rejected_accounts=0,
                provider_duplicates=0,
                provider_errors=parsed.errors,
                gaps=[],
                accounts=[],
                skipped_accounts=parsed.skipped,
            )
        import_id = self._db.insert(
            si,
            source="simplefin",
            file_hash=digest,
            original_bytes=raw,  # pending rows live only here (ADR 0014)
            imported_at=datetime.now(UTC),
        )
        self._db.bump_ledger_version()  # a stored Statement Import, as in import_files
        added = known = changed = rejected = dupes = merged = 0
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
            merged += self._match(account_id)[0]
            dupes += self._record_duplicates(account_id, import_id, acc, new_ids)
            gap = self._reconcile(account_id, masked, acc)
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
            added=added - merged,
            matched=merged,
            already_known=known,
            changed_known=changed,
            rejected_accounts=rejected,
            provider_duplicates=dupes,
            provider_errors=parsed.errors,
            gaps=gaps,
            accounts=[AffectedAccount(i, m) for i, m in sorted(touched.items())],
            skipped_accounts=parsed.skipped,
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

    def unlinked_csv_accounts(self) -> dict[str, int]:
        """Accounts no synced Account is linked to, by masked number."""
        a = schema.account
        return {
            str(r.masked_number): int(r.id)
            for r in self._db.execute(
                self._db.select(a, a.c.id, a.c.masked_number).where(
                    a.c.provider_ref.is_(None)
                )
            )
        }

    def synced_account_linked(self, provider_account_id: str) -> bool:
        """True once that SimpleFIN Account is linked to a CSV Account."""
        a = schema.account
        ref = keyed_ref("simplefin:" + provider_account_id)
        return (
            self._db.execute(
                self._db.select(a, a.c.id).where(
                    a.c.provider_ref == ref, a.c.external_ref != ref
                )
            ).first()
            is not None
        )

    def unlinked_synced_accounts(self) -> int:
        """Synced Accounts still standing alone (ADR 0014: two Accounts until
        the user links them)."""
        a = schema.account
        return len(
            self._db.execute(
                self._db.select(a, a.c.id).where(a.c.provider_ref == a.c.external_ref)
            ).all()
        )

    def ambiguous_matches(self) -> list[frozenset[int]]:
        """ADR 0014 ambiguous CSV-to-sync components, re-evaluated each import."""
        a = schema.account
        out: list[frozenset[int]] = []
        for (account_id,) in self._db.execute(
            self._db.select(a, a.c.id).where(a.c.provider_ref.is_not(None))
        ):
            out += self._match(account_id, merge=False)[1]
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
            .join(a, self._db.on(a, a.c.id == g.c.account_id))
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

    def _status_row(self) -> Any:
        t = schema.sync_status
        return self._db.execute(self._db.select(t)).first()

    def _set_status(self, **values: Any) -> None:
        t = schema.sync_status
        if self._status_row() is None:
            self._db.insert(t, **values)
        else:
            self._db.execute(self._db.update(t).values(**values))

    def record_sync_success(self, now: datetime) -> None:
        self._set_status(
            last_success_ts=int(now.timestamp()),
            last_failure_ts=None,
            failure_class=None,
            failures_in_a_row=0,
        )

    def record_sync_failure(self, error_class: str, now: datetime) -> None:
        """Only the exception class name is kept: messages may quote data."""
        row = self._status_row()
        self._set_status(
            last_failure_ts=int(now.timestamp()),
            failure_class=error_class[:64],
            failures_in_a_row=(row.failures_in_a_row if row else 0) + 1,
        )

    def record_consent_renewed(self, on: date) -> None:
        self._set_status(consent_renewed_on=on)

    def sync_status(self, today: date | None = None) -> SyncStatus:
        today = today or datetime.now(simplefin.LONDON).date()
        row = self._status_row()

        def at(ts: int | None) -> datetime | None:
            return None if ts is None else datetime.fromtimestamp(ts, UTC)

        renewed = row.consent_renewed_on if row else None
        expires = renewed + timedelta(days=simplefin.CONSENT_DAYS) if renewed else None
        left = (expires - today).days if expires else None
        return SyncStatus(
            last_sync_at=at(row.last_success_ts if row else None),
            last_failure_at=at(row.last_failure_ts if row else None),
            failure_class=row.failure_class if row else None,
            failures_in_a_row=row.failures_in_a_row if row else 0,
            consent_renewed_on=renewed,
            consent_expires_on=expires,
            consent_days_left=left,
            consent_warning=left is not None and left <= simplefin.CONSENT_WARN_DAYS,
        )

    def alert_is_new(self, key: str | None) -> bool:
        """Remember the alert last shown; True only when `key` differs from it,
        so an unchanged problem is not announced again on every run. Pass None
        when all is well: it clears the memory (and returns True, which the
        caller ignores), so the same problem recurring later is announced."""
        row = self._status_row()
        if (row.notified if row else None) == key:
            return False
        self._set_status(notified=key)
        return True

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

    def _match(
        self, account_id: int, *, merge: bool = True
    ) -> tuple[int, list[frozenset[int]]]:
        """ADR 0014 CSV-to-sync matching over the Account's unmatched rows.
        Pairs merge into the CSV Transaction; returns (pairs merged, ambiguous
        components)."""
        a, t, k = schema.account, schema.transaction, schema.synced_key
        linked = self._db.execute(
            self._db.select(a, a.c.provider_ref).where(a.c.id == account_id)
        ).scalar()
        if linked is None:
            return 0, []
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
            .outerjoin(k, self._db.on(k, k.c.transaction_id == t.c.id))
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
        merged = 0
        complete = self._coverage(account_id) if groups else {}
        for group in groups.values():
            for component in _components(group):
                must = {
                    m.id
                    for m in component
                    if _within(
                        complete["csv" if m.synced else "simplefin"], m.posted_on
                    )
                }
                pairs = _pair(component, must)
                if pairs is None:
                    ambiguous.append(frozenset(m.id for m in component))
                elif merge:
                    for c, s in pairs:
                        self._merge(c, s)
                    merged += len(pairs)
        return merged, ambiguous

    def _coverage(self, account_id: int) -> dict[str, list[tuple[date, date]]]:
        """ADR 0014 amendment: per source, the days each Statement Import holds
        completely for the Account: from its first row's date (a sync asks from
        London midnight; a CSV export may start mid-day, so not its first day)
        to the day before its last row's (that day may still gain rows). A CSV
        export whose dates are out of Bank order shows Bank order is not date
        order, so no CSV export of the Account gives coverage."""
        t, ts, si, k = (
            schema.transaction,
            schema.transaction_source,
            schema.statement_import,
            schema.synced_key,
        )
        days: dict[tuple[str, int], list[tuple[int, date]]] = defaultdict(list)
        for r in self._db.execute(
            self._db.select(
                ts,
                si.c.source,
                ts.c.statement_import_id,
                ts.c.bank_position,
                t.c.posted_on,
                k.c.posted_ts,
            )
            .join(si, self._db.on(si, si.c.id == ts.c.statement_import_id))
            .join(t, self._db.on(t, t.c.id == ts.c.transaction_id))
            .outerjoin(k, self._db.on(k, k.c.transaction_id == t.c.id))
            .where(t.c.account_id == account_id)
        ):
            on = r.posted_on
            if r.source == "simplefin" and r.posted_ts is not None:
                on = datetime.fromtimestamp(int(r.posted_ts), simplefin.LONDON).date()
            days[(str(r.source), int(r.statement_import_id))].append(
                (int(r.bank_position), on)
            )
        unordered = any(
            [d for _, d in sorted(rows)] != sorted(d for _, d in rows)
            for (source, _), rows in days.items()
            if source == "csv"
        )
        out: dict[str, list[tuple[date, date]]] = defaultdict(list)
        for (source, _), rows in days.items():
            if source == "csv" and unordered:
                continue
            ds = [d for _, d in rows]
            lo = min(ds) + timedelta(0 if source == "simplefin" else 1)
            hi = max(ds) - timedelta(1)
            if lo <= hi:
                out[source].append((lo, hi))
        return out

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
            schema.note,
        ):
            self._db.execute(
                self._db.update(table)
                .where(table.c.transaction_id == synced_id)
                .values(transaction_id=csv_id)
            )
        self._db.execute(self._db.delete(t).where(t.c.id == synced_id))
        self._one_live_note(csv_id)

    def _reconcile(
        self,
        account_id: int,
        masked: str,
        acc: simplefin.SyncedAccount,
    ) -> SyncGap | None:
        """ADR 0014: B_k - B_1 - sum of Transactions with instant in (t_1, t_k].
        B is the reported balance less pending rows: spike #26 found it includes
        them (balance == available-balance with a pending row present)."""
        balance = acc.balance_minor - acc.pending_minor
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
                balance_minor=balance,
                balance_ts=acc.balance_ts,
            )
            return None
        t, k = schema.transaction, schema.synced_key
        moved = 0
        for r in self._db.execute(
            self._db.select(t, t.c.posted_on, t.c.amount_minor, k.c.posted_ts)
            .outerjoin(k, self._db.on(k, k.c.transaction_id == t.c.id))
            .where(t.c.account_id == account_id)
        ):
            instant = (
                r.posted_ts if r.posted_ts is not None else _end_of_day_ts(r.posted_on)
            )
            if first.balance_ts < instant <= acc.balance_ts:
                moved += r.amount_minor
        gap = balance - first.balance_minor - moved
        if not gap:
            return None
        return SyncGap(
            AffectedAccount(account_id, masked),
            datetime.fromtimestamp(acc.balance_ts, UTC),
            _decimal(gap),
        )

    def spending_by_category(
        self, month: str, account_id: int | None = None
    ) -> list[Spend]:
        """Spending minus Refunds received in `month` (ADR 0004), by Category,
        then Subcategory, then Merchant ("Unknown" when none), largest first."""
        amounts = self._in_gbp(month, account_id)
        tree: dict[str, dict[str, dict[tuple[int | None, str], int]]] = {}
        for txn_id, c in self._categorisations().items():
            if txn_id in amounts and c.category and c.kind in ("Spending", "Refund"):
                own, gbp = amounts[txn_id]
                for (top, sub), minor in _shares_in_gbp(_shares(c, own), own, gbp):
                    merchants = tree.setdefault(top, {}).setdefault(sub, {})
                    key = (c.merchant_id, c.merchant or "Unknown")
                    merchants[key] = merchants.get(key, 0) - minor

        def level(nodes: dict[Any, Any]) -> tuple[Spend, ...]:
            out: list[Spend] = []
            for name, v in nodes.items():
                if isinstance(v, int):  # a Merchant's minor-unit total
                    merchant_id, label = name
                    out.append(Spend(label, _decimal(v), merchant_id=merchant_id))
                else:
                    kids = level(v)
                    total = sum((k.amount for k in kids), Decimal(0))
                    out.append(Spend(name, total, kids))
            return tuple(sorted(out, key=lambda s: (-s.amount, s.name)))

        return list(level(tree))

    def category_detail(
        self,
        name: str,
        month: str,
        trend: int = 6,
        top_merchants: int = 5,
        account_id: int | None = None,
    ) -> CategoryDetail | None:
        """None when the month has nothing in Category `name`."""
        start, end = _month_bounds(month)

        def total_in(m: str) -> Spend | None:
            return next(
                (s for s in self.spending_by_category(m, account_id) if s.name == name),
                None,
            )

        here = total_in(month)
        if here is None:
            return None
        months = [
            f"{(end.year * 12 + end.month - 2 - i) // 12}-"
            f"{(end.month - 2 - i) % 12 + 1:02d}"
            for i in range(trend - 1, -1, -1)
        ]
        series = tuple(
            (m, s.amount if (s := total_in(m)) else Decimal(0)) for m in months
        )
        txns = tuple(
            t
            for t in reversed(self.transactions())
            if start <= t.posted_on < end
            and account_id in (None, t.account_id)
            and (c := t.categorisation)
            and c.kind in ("Spending", "Refund")
            and c.category
            and any(top == name for (top, _), _ in _shares(c, 0))
        )
        return CategoryDetail(
            name,
            here.amount,
            here.children,
            _top_merchants(here.children, top_merchants),
            series,
            txns,
        )

    def category_breakdown(
        self, month: str, account_id: int | None = None
    ) -> Breakdown:
        start, end = _month_bounds(month)
        spends = self.spending_by_category(month, account_id)
        refunds = fees = Decimal(0)
        # per bucket: Refunds with no Category to net against (<= 0)
        unmatched = {UNCATEGORISED: Decimal(0), TO_REVIEW: Decimal(0)}
        mine = None
        if account_id is not None:
            tt = schema.transaction
            mine = set(
                self._db.execute(
                    self._db.select(tt, tt.c.id).where(*_only(account_id))
                ).scalars()
            )
        amounts = self._in_gbp(month, account_id)
        for t in self.transactions():
            c = t.categorisation
            if c is None or not start <= t.posted_on < end or t.id not in amounts:
                continue  # outside the month, or rate needed
            if mine is not None and t.id not in mine:
                continue
            own, gbp = amounts[t.id]
            if c.kind == "Fee":
                fees -= _decimal(gbp)
            elif c.kind == "Refund":
                refunds += _decimal(gbp)
                # only the Uncategorised and To review shares: a split
                # Refund's other parts are matched in their own Categories
                for (top, _), minor in _shares_in_gbp(_shares(c, own), own, gbp):
                    if top in unmatched:
                        unmatched[top] -= _decimal(minor)
        cats: list[Spend] = []
        for sp in spends:
            if sp.name in unmatched:  # gross, so never negative on the ring
                sp = replace(sp, amount=sp.amount - unmatched[sp.name])
                if sp.amount == 0:
                    continue
            cats.append(sp)
        total = sum((sp.amount for sp in spends), Decimal(0))
        return Breakdown(
            tuple(cats),
            sum(unmatched.values(), Decimal(0)),
            total,
            refunds,
            fees,
            _top_merchants((sub for sp in spends for sub in sp.children), 5),
        )

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
                m.c.always_ask,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
                sub.c.taxonomy_version.label("version"),
            )
            .outerjoin(sub, self._db.on(sub, sub.c.id == m.c.default_category_id))
            .outerjoin(top, self._db.on(top, top.c.id == sub.c.parent_id))
            .order_by(m.c.name)
        )
        return [
            Merchant(
                r.id,
                r.name,
                current(r.version, (r.top, r.sub)) if r.sub is not None else None,
                r.always_ask,
            )
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
                m.c.always_ask,
                c.c.source,
                c.c.taxonomy_version,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
                sub.c.taxonomy_version.label("version"),
            )
            .outerjoin(m, self._db.on(m, m.c.id == c.c.merchant_id))
            .outerjoin(sub, self._db.on(sub, sub.c.id == c.c.category_id))
            .outerjoin(top, self._db.on(top, top.c.id == sub.c.parent_id))
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
            category = current(r.version, (r.top, r.sub)) if r.sub is not None else None
            source = r.source
            if r.kind in CATEGORISED_KINDS and (r.always_ask or source == "always_ask"):
                # the categoriser abstained (#125); held here too, so a flag set
                # before the next `categorise.run` already counts as To review
                category, source = (TO_REVIEW, TO_REVIEW), "always_ask"
            # Corrections beat every rule (ADR 0005); Kinds without a Category
            # (Income, Transfer, Fee) ignore them. A flagged Merchant's
            # Merchant-wide Correction is off; per-Transaction ones stay.
            fix = by_txn.get(r.transaction_id) or (
                None if r.always_ask else by_merchant.get(r.merchant_id)
            )
            if fix and r.kind in CATEGORISED_KINDS:
                category, source = fix, "correction"
            out[r.transaction_id] = Categorisation(
                r.kind, r.merchant, category, source, r.taxonomy_version, r.merchant_id
            )
        return self._split_overlay(out, set(by_txn))

    def _split_overlay(
        self, out: dict[int, Categorisation], corrected: set[int]
    ) -> dict[int, Categorisation]:
        """Live Splits beat every Category Correction on their Transaction; a
        Refund linked to one counts under its largest part until the user picks
        a part with a Correction (`corrected`)."""
        splits = {
            s.transaction_id: s
            for s in self.splits()
            if (c := out.get(s.transaction_id)) and c.kind in CATEGORISED_KINDS
        }
        if not splits:
            return out
        for s in splits.values():
            parts = (SplitPart(s.amount_minor, s.largest.category),)
            out[s.transaction_id] = replace(
                out[s.transaction_id],
                category=s.largest.category,
                source="split",
                parts=parts if s.needs_recheck else s.parts,
            )
        t = schema.transaction
        rows = {
            r.id: r
            for r in self._db.execute(
                self._db.select(
                    t, t.c.id, t.c.posted_on, t.c.account_id, t.c.amount_minor
                )
            )
        }
        for refund_id, c in out.items():
            if (
                c.kind != "Refund"
                or c.merchant_id is None
                or refund_id in corrected
                or c.source == "split"  # the user split the Refund itself
            ):
                continue
            purchase = self._refunded_split(rows[refund_id], c.merchant_id, out, rows)
            if purchase is not None:
                out[refund_id] = replace(
                    c,
                    category=splits[purchase].largest.category,
                    source="split_refund",
                    refund_of=purchase,
                )
        return out

    @staticmethod
    def _refunded_split(
        refund: Any,
        merchant_id: int,
        out: dict[int, Categorisation],
        rows: dict[int, Any],
    ) -> int | None:
        """The split purchase a Refund is for, if any.
        shortcut: there is no Refund link yet (ADR 0004 "when matched"), so this
        is the latest split Spending of the same Merchant and Account on or
        before the Refund, no smaller than it; use the real link once it lands."""
        found = [
            (p.posted_on, p.id)
            for i, c in out.items()
            if c.source == "split"
            and c.kind == "Spending"
            and c.merchant_id == merchant_id
            and (p := rows[i]).account_id == refund.account_id
            and p.posted_on <= refund.posted_on
            and -p.amount_minor >= refund.amount_minor
        ]
        return max(found)[1] if found else None

    def ledger_version(self) -> int:
        """Bumped by every import that stores a file and every Correction."""
        return self._db.ledger_version()

    def correct_transaction(
        self, transaction_id: int, category: tuple[str, str]
    ) -> Correction:
        """Give one Transaction this Category, over any rule or Merchant."""
        t, c = schema.transaction, schema.categorisation
        self._exists(self._db.select(t, t.c.id).where(t.c.id == transaction_id))
        kind = self._db.execute(
            self._db.select(c, c.c.kind).where(c.c.transaction_id == transaction_id)
        ).scalar()
        category_id = self._category_id(category)  # an unknown one is a 404 first
        if kind is not None and kind not in CATEGORISED_KINDS:
            raise KindHasNoCategory(f"{kind} takes no Category")
        return self._correct(category_id, transaction_id=transaction_id)

    def correct_merchant(
        self, merchant_id: int, category: tuple[str, str]
    ) -> Correction:
        """Give every Transaction of this Merchant (now and future) this Category;
        a Correction for one Transaction still beats it."""
        m = schema.merchant
        flagged = self._db.execute(
            self._db.select(m, m.c.always_ask).where(m.c.id == merchant_id)
        ).first()
        if flagged is None:
            raise UnknownTarget("no such Merchant")
        category_id = self._category_id(category)
        if flagged.always_ask:
            raise AlwaysAskMerchant("an Always ask Merchant is corrected per payment")
        return self._correct(category_id, merchant_id=merchant_id)

    def set_always_ask(
        self, merchant_id: int, on: bool, source: str = "user"
    ) -> list[int]:
        """Flag or unflag a Merchant "Always ask" (#125); only ever on the
        user's word. Turning it on reverts the Merchant's Merchant-wide
        Corrections and returns their ids; per-Transaction ones stay. Turning
        it off restores nothing. Run `categorise.run` afterwards."""
        if source not in ALWAYS_ASK_SOURCES:
            raise ValueError("source is 'user' or 'suggestion'")
        m, k = schema.merchant, schema.correction
        self._exists(self._db.select(m, m.c.id).where(m.c.id == merchant_id))
        self._db.execute(
            self._db.update(m)
            .where(m.c.id == merchant_id)
            .values(always_ask=source if on else None)
        )
        reverted: list[int] = []
        if on:
            reverted = list(
                self._db.execute(
                    self._db.select(k, k.c.id).where(
                        k.c.merchant_id == merchant_id,
                        k.c.kind == "category",
                        k.c.reverted_at.is_(None),
                    )
                ).scalars()
            )
            self._db.execute(
                self._db.update(k)
                .where(k.c.id.in_(reverted))
                .values(reverted_at=datetime.now(UTC))
            )
        self._db.bump_ledger_version()
        return reverted

    def always_ask_suggested(self) -> set[int]:
        """Unflagged Merchants whose active Corrections (per-Transaction, on
        their Transactions, or Merchant-wide) name `SUGGEST_AFTER` or more
        distinct Categories. Only a suggestion: nothing here sets the flag."""
        c = schema.categorisation
        merchant_of = {
            r.transaction_id: r.merchant_id
            for r in self._db.execute(
                self._db.select(c, c.c.transaction_id, c.c.merchant_id)
            )
        }
        # oldest first: a later Correction of the same target replaces it
        live: dict[tuple[int | None, int | None], Correction] = {
            (k.transaction_id, k.merchant_id): k
            for k in self.corrections()
            if k.reverted_at is None
        }
        seen: dict[int, set[tuple[str, str]]] = {}
        for k in live.values():
            mid = k.merchant_id or merchant_of.get(k.transaction_id or 0)
            if mid is not None:
                seen.setdefault(mid, set()).add(k.category)
        flagged = {m.id for m in self.merchants() if m.always_ask}
        return {m for m, cats in seen.items() if len(cats) >= SUGGEST_AFTER} - flagged

    def revert_correction(self, correction_id: int) -> None:
        """Undo a Category Correction; it stays in the history, marked
        reverted. A Split is undone with `revert_split`."""
        self._revert(correction_id, "category")

    def _revert(self, correction_id: int, kind: str) -> None:
        k = schema.correction
        res = self._db.execute(
            self._db.update(k)
            .where(k.c.id == correction_id, k.c.kind == kind, k.c.reverted_at.is_(None))
            .values(reverted_at=datetime.now(UTC))
        )
        if res.rowcount != 1:
            raise UnknownTarget("no such active Correction")
        self._db.bump_ledger_version()

    # --- Splits (#123) ---------------------------------------------------------

    def preview_split(
        self,
        transaction_id: int,
        parts: Sequence[tuple[int | None, tuple[str, str]]],
    ) -> tuple[SplitPart, ...]:
        """The parts as they would be saved, the last one's amount computed as
        the remainder when it is None. Writes nothing."""
        t, c = schema.transaction, schema.categorisation
        total = self._exists(
            self._db.select(t, t.c.amount_minor).where(t.c.id == transaction_id)
        )
        cats = [(cat[0], cat[1]) for _, cat in parts]
        for cat in cats:
            self._category_id(cat)  # an unknown one is a 404 first
        kind = self._db.execute(
            self._db.select(c, c.c.kind).where(c.c.transaction_id == transaction_id)
        ).scalar()
        if kind is not None and kind not in CATEGORISED_KINDS:
            raise KindHasNoCategory(f"{kind} takes no Category")
        if transaction_id in self._transfer_legs():
            raise InvalidSplit("a Transfer cannot be split")
        amounts = split_parts(total, [a for a, _ in parts])
        return tuple(SplitPart(a, cat) for a, cat in zip(amounts, cats, strict=True))

    def split_transaction(
        self,
        transaction_id: int,
        parts: Sequence[tuple[int | None, tuple[str, str]]],
    ) -> Split:
        """Split one Transaction into `parts`; a live Split is replaced (it is
        reverted and stays in the history)."""
        made = self.preview_split(transaction_id, parts)
        if (live := self.split(transaction_id)) is not None:
            self._revert(live.id, "split")
        return self._write_split(transaction_id, made, [live] if live else [])

    def revert_split(self, transaction_id: int) -> int:
        """Undo the live Split as a whole; returns its Correction id."""
        live = self.split(transaction_id)
        if live is None:
            raise UnknownTarget("no live Split")
        self._revert(live.id, "split")
        return live.id

    def restore_split(self, correction_id: int) -> Split:
        """Bring a reverted Split back as a new Correction with the same parts,
        as they were (an undo; a Split that needed re-checking still does)."""
        old = next((s for s in self._splits(live=False) if s.id == correction_id), None)
        if old is None or old.reverted_at is None:
            raise UnknownTarget("no such reverted Split")
        if (live := self.split(old.transaction_id)) is not None:
            self._revert(live.id, "split")
        carry = [live, old] if live else [old]
        return self._write_split(old.transaction_id, old.parts, carry)

    def split(self, transaction_id: int) -> Split | None:
        """The Transaction's live Split, if any."""
        return next(
            (s for s in self.splits() if s.transaction_id == transaction_id), None
        )

    def splits(self) -> list[Split]:
        """Every live Split, oldest first."""
        return self._splits(live=True)

    def _splits(self, live: bool) -> list[Split]:
        k, p, t = schema.correction, schema.split_part, schema.transaction
        sub, top = schema.category.alias("sub"), schema.category.alias("top")
        stmt = (
            self._db.select(
                k,
                k.c.id,
                k.c.transaction_id,
                k.c.created_at,
                k.c.reverted_at,
                t.c.amount_minor.label("total"),
                t.c.currency,
                p.c.amount_minor,
                top.c.name.label("top"),
                sub.c.name.label("sub"),
                sub.c.taxonomy_version.label("version"),
            )
            .join(p, self._db.on(p, p.c.correction_id == k.c.id))
            .join(t, self._db.on(t, t.c.id == k.c.transaction_id))
            .join(sub, self._db.on(sub, sub.c.id == p.c.category_id))
            .join(top, self._db.on(top, top.c.id == sub.c.parent_id))
            .where(k.c.kind == "split")
            .order_by(k.c.id, p.c.ordinal)
        )
        if live:
            stmt = stmt.where(k.c.reverted_at.is_(None))
        rows: dict[int, list[Any]] = {}
        for r in self._db.execute(stmt):
            rows.setdefault(r.id, []).append(r)
        out: list[Split] = []
        for cid, rs in rows.items():
            parts = tuple(
                SplitPart(r.amount_minor, current(r.version, (r.top, r.sub)))
                for r in rs
            )
            r = rs[0]
            out.append(
                Split(
                    cid,
                    r.transaction_id,
                    parts,
                    r.created_at,
                    r.reverted_at,
                    r.total,
                    sum(x.amount_minor for x in parts) != r.total,
                    r.currency,
                )
            )
        return out

    def _write_split(
        self,
        transaction_id: int,
        parts: Sequence[SplitPart],
        carry: Sequence[Split] = (),
    ) -> Split:
        """Write a live Split. Part notes of each Split in `carry` move to it only
        where the part at that position is unchanged, so a note never lands on a
        different purpose; the rest stay with their own (reverted) Split."""
        cid = self._db.insert(
            schema.correction,
            kind="split",
            transaction_id=transaction_id,
            created_at=datetime.now(UTC),
        )
        for i, part in enumerate(parts):
            self._db.insert(
                schema.split_part,
                correction_id=cid,
                ordinal=i,
                amount_minor=part.amount_minor,
                category_id=self._category_id(part.category),
            )
        n = schema.note
        for old in carry:
            for i, part in enumerate(old.parts):
                if i < len(parts) and parts[i] == part:
                    self._db.execute(
                        self._db.update(n)
                        .where(n.c.split_id == old.id, n.c.part == i)
                        .values(split_id=cid)
                    )
        self._one_live_note(transaction_id)
        self._db.bump_ledger_version()
        return next(s for s in self.splits() if s.id == cid)

    # --- Purpose notes (#124) ------------------------------------------------

    def set_note(self, transaction_id: int, text: str, part: int | None = None) -> Note:
        """Write a new live version of the note on the Transaction (or on part
        `part` of its live Split); the previous one is kept, superseded."""
        _check_note(text)
        t, n = schema.transaction, schema.note
        self._exists(self._db.select(t, t.c.id).where(t.c.id == transaction_id))
        split_id = None
        if part is not None:
            live = self.split(transaction_id)
            if live is None or not 0 <= part < len(live.parts):
                raise UnknownTarget("no such Split part")
            split_id = live.id
        now = datetime.now(UTC)
        self._db.execute(
            self._db.update(n)
            .where(
                *self._note_target(transaction_id, part), n.c.superseded_at.is_(None)
            )
            .values(superseded_at=now)
        )
        self._db.insert(
            n,
            transaction_id=transaction_id,
            part=part,
            split_id=split_id,
            text=text,
            created_at=now,
        )
        self._db.bump_ledger_version()
        return self.note_history(transaction_id, part)[-1]

    def note(self, transaction_id: int, part: int | None = None) -> Note | None:
        """The live note on the target, if any."""
        h = self.note_history(transaction_id, part)
        return h[-1] if h and h[-1].superseded_at is None else None

    def note_history(self, transaction_id: int, part: int | None = None) -> list[Note]:
        """Every version on the target, oldest first; the last may be live."""
        n = schema.note
        rows = self._db.execute(
            self._db.select(
                n,
                n.c.id,
                n.c.transaction_id,
                n.c.part,
                n.c.text,
                n.c.created_at,
                n.c.superseded_at,
            )
            .where(*self._note_target(transaction_id, part))
            .order_by(n.c.id)
        ).all()
        return [Note(*r) for r in rows]

    def delete_note(self, transaction_id: int, part: int | None = None) -> int:
        """Purge every version of the note on the target; returns how many."""
        res = self._db.execute(
            self._db.delete(schema.note).where(*self._note_target(transaction_id, part))
        )
        if not res.rowcount:
            raise UnknownTarget("no note")
        self._db.bump_ledger_version()
        return res.rowcount

    def _note_target(
        self, transaction_id: int, part: int | None
    ) -> list[ColumnElement[bool]]:
        """The rows of one note: the Transaction's own, or part `part` of its
        live Split (none when it has no live Split)."""
        n = schema.note
        if part is None:
            return [n.c.transaction_id == transaction_id, n.c.part.is_(None)]
        live = self.split(transaction_id)
        if live is None:
            return [n.c.id.is_(None)]  # matches nothing; false() drops the user scope
        return [
            n.c.transaction_id == transaction_id,
            n.c.part == part,
            n.c.split_id == live.id,
        ]

    def _one_live_note(self, transaction_id: int) -> None:
        """After a merge or a carried-over Split, a target can hold two live
        versions; the newest stays live and the others become its history."""
        n = schema.note
        rows = self._db.execute(
            self._db.select(n, n.c.id, n.c.part, n.c.split_id)
            .where(n.c.transaction_id == transaction_id, n.c.superseded_at.is_(None))
            .order_by(n.c.id)
        ).all()
        newest = {(r.part, r.split_id): r.id for r in rows}
        stale = [r.id for r in rows if newest[(r.part, r.split_id)] != r.id]
        if stale:
            self._db.execute(
                self._db.update(n)
                .where(n.c.id.in_(stale))
                .values(superseded_at=datetime.now(UTC))
            )

    def corrections(self) -> list[Correction]:
        """Every Category Correction, reverted ones included, oldest first."""
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
                sub.c.taxonomy_version.label("version"),
                k.c.created_at,
                k.c.reverted_at,
            )
            .join(sub, self._db.on(sub, sub.c.id == k.c.category_id))
            .join(top, self._db.on(top, top.c.id == sub.c.parent_id))
            .where(k.c.kind == "category")
            .order_by(k.c.id)
        )
        return [
            Correction(
                r.id,
                r.transaction_id,
                r.merchant_id,
                current(r.version, (r.top, r.sub)),  # made on any version
                r.created_at,
                r.reverted_at,
            )
            for r in rows
        ]

    def _category_id(self, category: tuple[str, str]) -> int:
        sub, top = schema.category, schema.category.alias("top")
        latest = self._db.execute(
            self._db.select(sub, func.max(sub.c.taxonomy_version))
        ).scalar()
        return self._exists(  # a leaf of the current taxonomy version only
            self._db.select(sub, sub.c.id)
            .join(top, self._db.on(top, top.c.id == sub.c.parent_id))
            .where(
                top.c.name == category[0],
                sub.c.name == category[1],
                sub.c.taxonomy_version == latest,
            )
        )

    def _correct(self, category_id: int, **target: int) -> Correction:
        cid = self._db.insert(
            schema.correction,
            category_id=category_id,
            created_at=datetime.now(UTC),
            **target,
        )
        self._db.bump_ledger_version()
        return next(k for k in self.corrections() if k.id == cid)

    def subscriptions(self) -> list[SubscriptionEntry]:
        """Every registry entry, negative ones included, by Merchant name."""
        e, m = schema.subscription_entry, schema.merchant
        rows = self._db.execute(
            self._db.select(e, e, m.c.name.label("merchant"))
            .join(m, self._db.on(m, m.c.id == e.c.merchant_id))
            .where(e.c.reverted_at.is_(None))
            .order_by(e.c.id)
        )
        first: dict[int, datetime] = {}
        since: dict[int, datetime] = {}
        latest: dict[int, Any] = {}
        for r in rows:
            first.setdefault(r.merchant_id, r.created_at)
            prior = latest.get(r.merchant_id)
            # a detection that lost a race with a user write never wins
            if r.source == "detection" and prior is not None and prior.source == "user":
                continue
            if prior is None or prior.status != r.status:
                since[r.merchant_id] = r.created_at
            latest[r.merchant_id] = r
        return sorted(
            (
                SubscriptionEntry(
                    r.merchant_id,
                    r.merchant,
                    r.kind,
                    r.group_name,
                    r.status,
                    r.reason,
                    r.source,
                    r.evidence_on,
                    r.charge_count,
                    r.interval,
                    first[r.merchant_id],
                    r.created_at,
                    since[r.merchant_id],
                )
                for r in latest.values()
            ),
            key=lambda x: (x.merchant, x.merchant_id),
        )

    def subscription(self, merchant_id: int) -> SubscriptionEntry | None:
        return next(
            (x for x in self.subscriptions() if x.merchant_id == merchant_id), None
        )

    def add_subscription(self, merchant_id: int, kind: str, group: str) -> None:
        """The user lists a Merchant as a Subscription or Bill."""
        self._registry(
            merchant_id, "user", kind, group, "active", "user_added", date.today()
        )

    def regroup_subscription(self, merchant_id: int, kind: str, group: str) -> None:
        """The user moves an entry to another kind or group; the rest is kept."""
        x = self._listed(merchant_id)
        self._registry(
            merchant_id,
            "user",
            kind,
            group,
            x.status,
            x.reason,
            x.evidence_on,
            x.charge_count,
            x.interval,
        )

    def confirm_subscription(
        self, merchant_id: int, kind: str | None = None, group: str | None = None
    ) -> int:
        """The user says yes, it is one (to a suggestion, or after removing it).
        Returns the registry row written."""
        x = self.subscription(merchant_id)
        if x is None:
            raise UnknownTarget("no such registry entry")
        return self._registry(
            merchant_id,
            "user",
            kind or x.kind,
            group or x.group,
            "active",
            "user_confirmed",
            date.today(),
            x.charge_count,
            x.interval,
        )

    def cancel_subscription(self, merchant_id: int) -> int:
        """The user says they cancelled it: its kind, group and evidence stay,
        and it leaves the totals. Confirming it lists it as active again.
        Returns the registry row written."""
        x = self._listed(merchant_id)
        return self._registry(
            merchant_id,
            "user",
            x.kind,
            x.group,
            USER_CANCELLED,
            x.reason,
            x.evidence_on,
            x.charge_count,
            x.interval,
        )

    def not_a_subscription(self, merchant_id: int) -> int:
        """Remove or dismiss: remembered as "not a subscription", never deleted.
        Returns the registry row written."""
        return self._registry(
            merchant_id, "user", None, None, NOT_SUBSCRIPTION, None, date.today()
        )

    def revert_subscription_decision(self, entry_id: int) -> None:
        """Undo one user decision: the row stays as history, marked reverted,
        and the Merchant's entry is what it was before."""
        e = schema.subscription_entry
        res = self._db.execute(
            self._db.update(e)
            .where(e.c.id == entry_id, e.c.source == "user", e.c.reverted_at.is_(None))
            .values(reverted_at=datetime.now(UTC))
        )
        if res.rowcount != 1:
            raise UnknownTarget("no such active registry decision")
        self._db.bump_ledger_version()

    def record_detection(
        self,
        merchant_id: int,
        kind: str,
        group: str,
        reason: str,
        status: str,
        evidence_on: date,
        charge_count: int | None = None,
        interval: str | None = None,
    ) -> bool:
        """Recognition's write. A user decision always wins: once the user has
        touched this Merchant nothing is written. Repeating the latest
        detection writes nothing either. True when an entry was written."""
        if reason not in DETECTED_REASONS or status not in DETECTED_STATUSES:
            raise InvalidEntry("not a detection reason or status")
        e = schema.subscription_entry
        decided = self._db.execute(
            self._db.select(e, e.c.id).where(
                e.c.merchant_id == merchant_id,
                e.c.source == "user",
                e.c.reverted_at.is_(None),
            )
        ).first()
        if decided is not None:
            return False
        x = self.subscription(merchant_id)
        same = (kind, group, status, reason, evidence_on, charge_count, interval)
        if x is not None and same == (
            x.kind,
            x.group,
            x.status,
            x.reason,
            x.evidence_on,
            x.charge_count,
            x.interval,
        ):
            return False
        self._registry(merchant_id, "detection", *same)
        return True

    def _listed(self, merchant_id: int) -> SubscriptionEntry:
        x = self.subscription(merchant_id)
        if x is None or x.status == NOT_SUBSCRIPTION:
            raise UnknownTarget("not in the registry")
        return x

    def _registry(
        self,
        merchant_id: int,
        source: str,
        kind: str | None,
        group: str | None,
        status: str,
        reason: str | None,
        evidence_on: date,
        charge_count: int | None = None,
        interval: str | None = None,
    ) -> int:
        m = schema.merchant
        self._exists(self._db.select(m, m.c.id).where(m.c.id == merchant_id))
        if status != NOT_SUBSCRIPTION and (
            kind not in REGISTRY_KINDS or group not in TAXONOMY[REGISTRY_KINDS[kind]]
        ):
            raise InvalidEntry("kind and group must be a Subscriptions or Bills leaf")
        if interval is not None and interval not in INTERVALS:
            raise InvalidEntry("interval must be weekly, monthly or annual")
        entry_id = self._db.insert(
            schema.subscription_entry,
            merchant_id=merchant_id,
            source=source,
            kind=kind,
            group_name=group,
            status=status,
            reason=reason,
            evidence_on=evidence_on,
            charge_count=charge_count,
            interval=interval,
            created_at=datetime.now(UTC),
        )
        self._db.bump_ledger_version()
        return entry_id

    def _exists(self, stmt: Select[Any]) -> int:
        row = self._db.execute(stmt).first()
        if row is None:
            raise UnknownTarget("unknown target")
        return int(row[0])

    # --- FX with provenance (#156): rates are never fetched -----------------

    def set_rate(self, currency: str, rate: Decimal, on: date) -> fx.Rate:
        """Enter a rate: GBP per unit of `currency` from `on`. Re-entering one
        for the same currency and date replaces it."""
        if currency == fx.GBP or not fx.CURRENCY_CODE.fullmatch(currency):
            raise fx.InvalidRate("a rate is for a foreign currency code")
        if currency in ingest.NOT_HUNDREDTHS:  # never imported, so never used
            raise fx.InvalidRate(ingest.NOT_HUNDREDTHS_REASON)
        fx.check_rate(rate)
        r = schema.fx_rate
        self._db.execute(
            self._db.delete(r).where(r.c.currency == currency, r.c.rate_on == on)
        )
        self._db.insert(
            r,
            currency=currency,
            rate=str(rate),
            rate_on=on,
            created_at=datetime.now(UTC),
        )
        self._db.bump_ledger_version()
        return fx.Rate(currency, rate, on, "user")

    def fx_rates(self) -> list[fx.Rate]:
        """Rates implied by the user's own exchanges, then those entered,
        each oldest first."""
        return self._exchange_rates() + self._entered_rates()

    def _entered_rates(self) -> list[fx.Rate]:
        r = schema.fx_rate
        return [
            fx.Rate(x.currency, Decimal(x.rate), x.rate_on, "user")
            for x in self._db.execute(
                self._db.select(r, r.c.currency, r.c.rate, r.c.rate_on).order_by(
                    r.c.rate_on, r.c.currency
                )
            )
        ]

    def _exchange_rates(self) -> list[fx.Rate]:
        """Per day and currency: GBP paid or received over the foreign amount,
        across that day's exchange legs linked as Transfers. A day that
        exchanged GBP with more than one currency implies nothing: which GBP
        leg went with which is not knowable."""
        t = schema.transaction
        legs = {
            r.id: r
            for r in self._db.execute(
                self._db.select(
                    t, t.c.id, t.c.posted_on, t.c.amount_minor, t.c.currency
                ).where(t.c.bank_type == EXCHANGE)
            )
        }
        sums: dict[tuple[date, str], tuple[int, int]] = {}
        for a, b in self._transfer_legs().items():
            if a not in legs or b not in legs or legs[a].currency != fx.GBP:
                continue
            g, f = legs[a], legs[b]
            if f.currency == fx.GBP:
                continue
            gbp, fgn = sums.get((g.posted_on, f.currency), (0, 0))
            sums[(g.posted_on, f.currency)] = (
                gbp + abs(g.amount_minor),
                fgn + abs(f.amount_minor),
            )
        per_day = Counter(day for day, _ in sums)
        out: list[fx.Rate] = []
        for (day, cur), (gbp, fgn) in sorted(sums.items()):
            rate = Decimal(gbp) / Decimal(fgn)
            if per_day[day] == 1 and fx.MIN_RATE <= rate <= fx.MAX_RATE:
                out.append(fx.Rate(cur, rate, day, "exchange"))
        return out

    def _conversions(self, *where: ColumnElement[bool]) -> dict[int, Any]:
        """{id: (row, Conversion | None)} for each foreign-currency Transaction
        matching `where`; None is rate needed. The one place a rate is chosen."""
        t = schema.transaction
        rates = self.fx_rates()
        out: dict[int, Any] = {}
        for r in self._db.execute(
            self._db.select(
                t, t.c.id, t.c.posted_on, t.c.amount_minor, t.c.currency, t.c.gbp_minor
            ).where(t.c.currency != fx.GBP, *where)
        ):
            out[r.id] = (r, self._convert(r, rates))
        return out

    def foreign_in_gbp(self) -> dict[int, int | None]:
        """{Transaction id: GBP minor units} for every foreign-currency
        Transaction; None is rate needed."""
        return {
            i: None if c is None else c.gbp_minor
            for i, (_, c) in self._conversions().items()
        }

    @staticmethod
    def _convert(r: Any, rates: list[fx.Rate]) -> fx.Conversion | None:
        g, a = r.gbp_minor, r.amount_minor
        if g is not None and a and g and (g > 0) == (a > 0):
            rate = Decimal(g) / Decimal(a)
            if fx.MIN_RATE <= rate <= fx.MAX_RATE:  # the Bank's own: used as is
                bank = fx.Rate(r.currency, rate, r.posted_on, "bank")
                return fx.Conversion(r.currency, a, g, bank, r.id)
        picked = fx.pick(rates, r.currency, r.posted_on)
        if picked is None:
            return None
        gbp = fx.convert(a, picked.gbp_per_unit)
        return fx.Conversion(r.currency, a, gbp, picked, r.id)

    def _month_terms(
        self, month: str, account_id: int | None
    ) -> list[ColumnElement[bool]]:
        start, end = _month_bounds(month)
        t = schema.transaction
        return [t.c.posted_on >= start, t.c.posted_on < end, *_only(account_id)]

    def _in_gbp(self, month: str, account_id: int | None) -> dict[int, tuple[int, int]]:
        """{id: (amount in its own currency, in GBP)} for the month's
        Transactions; a rate-needed one is left out, so no total counts it."""
        t = schema.transaction
        terms = self._month_terms(month, account_id)
        out = {
            r.id: (r.amount_minor, r.amount_minor)
            for r in self._db.execute(
                self._db.select(t, t.c.id, t.c.amount_minor).where(
                    t.c.currency == fx.GBP, *terms
                )
            )
        }
        for txn_id, (_, c) in self._conversions(*terms).items():
            if c is not None:
                out[txn_id] = (c.amount_minor, c.gbp_minor)
        return out

    def fx_month(self, month: str, account_id: int | None = None) -> FxMonth:
        """The provenance of `month`'s GBP totals: the same choice `_in_gbp`
        makes, Transaction by Transaction."""
        found = self._conversions(*self._month_terms(month, account_id))
        needed = {i for i, (_, c) in found.items() if c is None}
        return FxMonth(
            tuple(c for _, (_, c) in sorted(found.items()) if c is not None),
            tuple(x for x in self._transactions(schema.transaction.c.id.in_(needed)))
            if needed
            else (),
        )

    def balances_in_gbp(self) -> BalancesInGbp:
        """Each Account's Bank balance in GBP: a foreign one at the rate on its
        last Transaction's date (an exchange rate or one entered)."""
        t = schema.transaction
        last: dict[int, date] = {
            r.account_id: r.last
            for r in self._db.execute(
                self._db.select(
                    t, t.c.account_id, func.max(t.c.posted_on).label("last")
                ).group_by(t.c.account_id)
            )
        }
        rates = self.fx_rates()
        total: int | None = 0
        foreign: list[AccountInGbp] = []
        for a in self.accounts():
            if a.bank_balance is None:
                total = None
                continue
            minor = int(a.bank_balance.scaleb(2))
            if a.currency == fx.GBP:
                total = None if total is None else total + minor
                continue
            on = last[a.id]
            picked = fx.pick(rates, a.currency, on)
            c = (
                None
                if picked is None
                else fx.Conversion(
                    a.currency, minor, fx.convert(minor, picked.gbp_per_unit), picked
                )
            )
            foreign.append(AccountInGbp(a.id, c))
            total = None if total is None or c is None else total + c.gbp_minor
        return BalancesInGbp(None if total is None else _decimal(total), tuple(foreign))

    def summarise_month(
        self, month: str, account_id: int | None = None
    ) -> MonthSummary:
        start, end = _month_bounds(month)
        moved = self._transfer_legs()  # neither spending nor income
        ins = outs = moved_in = moved_out = 0
        for txn_id, (_, gbp) in self._in_gbp(month, account_id).items():
            if txn_id in moved:
                moved_in, moved_out = moved_in + max(gbp, 0), moved_out - min(gbp, 0)
            else:
                ins, outs = ins + max(gbp, 0), outs - min(gbp, 0)
        return MonthSummary(
            month,
            CURRENCY,
            _decimal(ins),
            _decimal(outs),
            _decimal(ins - outs),
            [
                g
                for g in self.gaps()
                if g.from_date < end
                and g.to_date >= start
                and account_id in (None, g.account.id)
            ],
            _decimal(moved_in),
            _decimal(moved_out),
            sum(
                c is None
                for _, c in self._conversions(
                    *self._month_terms(month, account_id)
                ).values()
            ),
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

    def months(self) -> list[str]:
        """Months (YYYY-MM) that have Transactions, newest first."""
        t = schema.transaction
        days = self._db.execute(self._db.select(t, t.c.posted_on).distinct()).scalars()
        return sorted({f"{d:%Y-%m}" for d in days}, reverse=True)

    def accounts(self) -> list[AccountBalance]:
        """Accounts with Bank and Ledger balances; an empty Account is skipped.
        A CSV Account whose Bank gives no balance (HSBC) has None for both."""
        a = schema.account
        gaps = self.gaps()
        t = schema.transaction
        # shortcut: synced-only Accounts stay hidden (they reconcile through
        # sync_gaps(), ADR 0014) until the Accounts screen shows synced balances
        no_balance = set(
            self._db.execute(
                self._db.select(t, t.c.account_id)
                .where(t.c.origin == "csv", t.c.running_balance_minor.is_(None))
                .distinct()
            ).scalars()
        )
        si, ts, tx = (
            schema.statement_import,
            schema.transaction_source,
            schema.transaction,
        )
        latest: dict[int, tuple[datetime, str]] = {}
        for r in self._db.execute(
            self._db.select(si, tx.c.account_id, si.c.imported_at, si.c.source)
            .join(ts, self._db.on(ts, ts.c.statement_import_id == si.c.id))
            .join(tx, self._db.on(tx, tx.c.id == ts.c.transaction_id))
            .distinct()
        ):
            if r.account_id not in latest or r.imported_at > latest[r.account_id][0]:
                latest[r.account_id] = (r.imported_at, r.source)
        out: list[AccountBalance] = []
        for r in self._db.execute(
            self._db.select(
                a,
                a.c.id,
                a.c.masked_number,
                a.c.name,
                a.c.kind,
                a.c.currency,
                schema.bank.c.name.label("bank"),
            )
            .join(
                schema.bank, self._db.on(schema.bank, schema.bank.c.id == a.c.bank_id)
            )
            .order_by(a.c.id)
        ).all():
            rows = self._transactions_in_bank_order(r.id)
            if rows:
                opening = rows[0].running_balance_minor - rows[0].amount_minor
                bank: Decimal | None = _decimal(rows[-1].running_balance_minor)
                held: Decimal | None = _decimal(
                    opening + sum(x.amount_minor for x in rows)
                )
            elif r.id in no_balance:
                bank = held = None
            else:
                continue
            out.append(
                AccountBalance(
                    r.id,
                    r.masked_number,
                    r.name,
                    r.kind,
                    r.currency,
                    bank,
                    held,
                    [g for g in gaps if g.account.id == r.id],
                    r.bank,
                    *latest.get(r.id, (None, "")),
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
        # a currency exchange (#156): both legs EXCHANGE, same day, two of the
        # user's Accounts in different currencies; adjacent rows pair first
        candidates += [
            ((False, False, abs(i.id - o.id), o.id, i.id), o.id, i.id)
            for o in rows
            if o.amount_minor < 0 and o.bank_type == EXCHANGE
            for i in rows
            if i.amount_minor > 0
            and i.bank_type == EXCHANGE
            and i.currency != o.currency
            and i.account_id != o.account_id
            and i.posted_on == o.posted_on
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
            .join(a, self._db.on(a, a.c.id == g.c.account_id))
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
            .join(t, self._db.on(t, t.c.id == src.c.transaction_id))
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
            name=row.account_name or f"Account {row.masked_number}",
            kind="current",
            masked_number=row.masked_number,
            currency=row.currency,
        )
        return new
