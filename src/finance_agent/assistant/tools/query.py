"""The query planner (#271, ADR 0007, ADR 0015): a compound spending question
("what did my car cost me this year") becomes selectors the model chooses, and
the Engine resolves and sums them.

The **semantic layer** is the only vocabulary a plan may use, and the features
the model router (#284) reads (`QueryPlan.dimensions`):

- metrics: spend (after refunds, as the Category screen), count (Spending
  Transactions counted) and the Merchants counted;
- dimensions: Category, Merchant, keyword, time range and Account.

A Merchant counts when one of its Category leaves is a selected Category or
Subcategory, or it is a Merchant that a Merchant name or keyword found through
hybrid search (#253, whole words only). The total is the sum of the rows
listed (plus `other` past MAX_ROWS), so the model never adds figures. A
Merchant in the same top-level Categories that was left out is listed as
`not_counted`, for the user to add.

A `QueryPlan` holds selectors and ids, never figures: the graph keeps the last
one in the thread so a follow-up ("of that, how much was fuel") narrows it."""

import hashlib
from collections.abc import Iterator, Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finance_agent.assistant.tools.read import (
    MAX_ROWS,
    Data,
    Money,
    Month,
    NoData,
    Source,
    _category,  # pyright: ignore[reportPrivateUsage]
)
from finance_agent.categorise.display_names import display_name
from finance_agent.engine.search import exact, search
from finance_agent.ledger import CURRENCY, Ledger
from finance_agent.ledger.taxonomy import TAXONOMY

METRICS = ("spend", "count", "merchants")
DIMENSIONS = ("category", "merchant", "keyword", "time_range", "account")
MAX_MONTHS = 24
MAX_SELECTORS = 12
NOT_COUNTED = 5
SEARCH_DEPTH = 50  # hits read per Merchant name or keyword
_STRICT = ConfigDict(extra="forbid", frozen=True)
_CATEGORIES = "; ".join(f"{top}: {', '.join(subs)}" for top, subs in TAXONOMY.items())


class QueryPlan(BaseModel):
    """A resolved plan: selectors and ids only, never a figure. Typed so the
    model router (#284) can read its features."""

    model_config = _STRICT
    categories: tuple[str, ...] = ()  # "Top" or "Top > Sub", taxonomy names
    merchants: tuple[str, ...] = ()  # as asked, sanitised
    keywords: tuple[str, ...] = ()
    merchant_ids: tuple[int, ...] = ()  # what the names and keywords found
    since: str
    until: str
    account_id: int | None = None
    within: "QueryPlan | None" = None  # the plan a follow-up narrows

    def dimensions(self) -> frozenset[str]:
        """The semantic layer's dimensions this plan uses."""
        used = {"time_range"}
        for name, on in (
            ("category", self.categories),
            ("merchant", self.merchants),
            ("keyword", self.keywords),
            ("account", self.account_id is not None),
        ):
            if on:
                used.add(name)
        return frozenset(used | (self.within.dimensions() if self.within else set()))

    def counts(self, top: str, sub: str, merchant_id: int | None) -> bool:
        """Does this plan count a Merchant's spending in Category leaf top > sub?"""
        here = (
            top in self.categories
            or f"{top} > {sub}" in self.categories
            or (merchant_id is not None and merchant_id in self.merchant_ids)
        )
        return here and (
            self.within is None or self.within.counts(top, sub, merchant_id)
        )

    def months(self) -> Iterator[str]:
        y, m = int(self.since[:4]), int(self.since[5:])
        while (s := f"{y}-{m:02d}") <= self.until:
            yield s
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)


class SpendForQueryIn(BaseModel):
    model_config = _STRICT
    categories: list[str] = Field(
        default=[],
        max_length=MAX_SELECTORS,
        description="top-level Categories (Transport) or Subcategories as"
        ' "Top > Sub" (Transport > Fuel) whose spending counts. Categories, as'
        " top-level: subcategories: " + _CATEGORIES,
    )
    merchants: list[str] = Field(
        default=[],
        max_length=MAX_SELECTORS,
        description="Merchant names whose spending counts, e.g. DVLA, Halfords",
    )
    keywords: list[str] = Field(
        default=[],
        max_length=MAX_SELECTORS,
        description="words in a Transaction or Merchant, e.g. MOT; each whole word"
        " must appear",
    )
    since: Month | None = Field(
        default=None, description="YYYY-MM the range starts; default `until`"
    )
    until: Month | None = Field(
        default=None, description="YYYY-MM the range ends; default the latest month"
    )
    account_id: int | None = Field(default=None, description="one Account only")
    follow_up: bool = Field(
        default=False,
        description="true to narrow this conversation's last spend_for_query"
        ' answer, e.g. "of that, how much was fuel"',
    )

    @model_validator(mode="after")
    def _something(self) -> "SpendForQueryIn":
        for s in (*self.categories, *self.merchants, *self.keywords):
            if not 0 < len(s.strip()) <= 60:
                raise ValueError("a selector is 1 to 60 characters")
        if not (self.categories or self.merchants or self.keywords or self.follow_up):
            raise ValueError("name at least one Category, Merchant or keyword")
        return self


class CountedRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    merchant_id: int | None  # None: spending with no Merchant
    merchant: Data
    amount: Money  # spending after refunds over the range


class SpendForQueryOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    since: str
    until: str
    total: Money  # = the sum of `merchants` plus `other`, by construction
    count: int  # Spending Transactions counted
    merchants: list[CountedRow]  # counted, largest first
    other: Money  # the counted Merchants past MAX_ROWS
    truncated: bool
    not_counted: list[CountedRow]  # same top-level Categories, left out
    unmatched: list[Data]  # selectors that found nothing
    plan: QueryPlan = Field(exclude=True)  # for the thread; never shown


def _money(minor: int) -> Money:
    return Money(minor=minor, currency=CURRENCY)


def _range(
    led: Ledger, inp: SpendForQueryIn, prev: QueryPlan | None
) -> tuple[str, str]:
    known = led.months()
    if not known:
        raise NoData("no transactions")
    until = inp.until or (prev.until if prev else None) or known[0]
    since = inp.since or (prev.since if prev and not inp.until else None) or until
    since, until = min(since, until), max(since, until)
    y, m = int(until[:4]), int(until[5:])
    floor = (y * 12 + m - 1) - (MAX_MONTHS - 1)
    return max(since, f"{floor // 12}-{floor % 12 + 1:02d}"), until


def _found(src: Source, led: Ledger, term: str) -> set[int]:
    """Merchant ids whose Merchant, Transaction, Note or Split text has every
    word of `term` as a whole word (no prefix matches: MOT is not MOTORWAY)."""
    by_txn: dict[int, int] | None = None
    out: set[int] = set()
    for h in search(src.conn, src.user_id, term, limit=SEARCH_DEPTH):
        if not exact(term, h.snippet.data):
            continue
        if h.kind == "merchant":
            out.add(h.ref)
        elif h.transaction_id is not None:
            if by_txn is None:
                by_txn = {
                    t.id: c.merchant_id
                    for t in led.transactions()
                    if (c := t.categorisation) and c.merchant_id is not None
                }
            if (mid := by_txn.get(h.transaction_id)) is not None:
                out.add(mid)
    return out


def plan(
    src: Source, inp: SpendForQueryIn, previous: QueryPlan | None = None
) -> tuple[QueryPlan, list[str]]:
    """Resolve the model's selectors into a plan; also the selectors that
    found nothing. `previous` is narrowed when the input is a follow-up."""
    led = Ledger(src.conn, src.user_id)
    prev = previous if inp.follow_up else None
    if prev is None and not (inp.categories or inp.merchants or inp.keywords):
        raise NoData("no earlier answer in this conversation to narrow")
    since, until = _range(led, inp, prev)
    unmatched: list[str] = []
    cats: list[str] = []
    for name in inp.categories:
        top, sub = _category(name)
        subs = TAXONOMY.get(top)
        leaf = next(
            (s for s in subs or () if sub and s.casefold() == sub.casefold()), None
        )
        if subs is None or (sub and leaf is None):
            unmatched.append(name)
        else:
            cats.append(f"{top} > {leaf}" if leaf else top)
    ids: set[int] = set()
    for term in (*inp.merchants, *inp.keywords):
        if found := _found(src, led, term):
            ids |= found
        else:
            unmatched.append(term)
    p = QueryPlan(
        categories=tuple(dict.fromkeys(cats)),
        merchants=tuple(Data(data=s).data for s in inp.merchants),
        keywords=tuple(Data(data=s).data for s in inp.keywords),
        merchant_ids=tuple(sorted(ids)),
        since=since,
        until=until,
        account_id=inp.account_id
        if inp.account_id is not None
        else (prev.account_id if prev else None),
        within=prev,
    )
    if prev and not (inp.categories or inp.merchants or inp.keywords):
        p = prev.model_copy(update={"since": since, "until": until})  # same set
    return p, unmatched


def spend(src: Source, p: QueryPlan, unmatched: Sequence[str] = ()) -> SpendForQueryOut:
    """The Engine's sum for a plan: every figure comes from the Ledger."""
    led = Ledger(src.conn, src.user_id)
    counted: dict[int | None, tuple[str, int]] = {}  # Merchant -> (name, minor)
    left: dict[tuple[str, int | None], tuple[str, int]] = {}  # (top, Merchant)
    tops = _tops(p)
    for month in p.months():
        for top in led.spending_by_category(month, p.account_id):
            for sub in top.children:
                for leaf in sub.children:
                    minor = int(leaf.amount.scaleb(2))
                    if p.counts(top.name, sub.name, leaf.merchant_id):
                        tops.add(top.name)
                        _, was = counted.get(leaf.merchant_id, (leaf.name, 0))
                        counted[leaf.merchant_id] = (leaf.name, was + minor)
                    else:
                        key = (top.name, leaf.merchant_id)
                        left[key] = (leaf.name, left.get(key, ("", 0))[1] + minor)
    near: dict[int | None, tuple[str, int]] = {}
    for (top, mid), (name, minor) in left.items():
        if top in tops and mid not in counted:
            near[mid] = (name, near.get(mid, ("", 0))[1] + minor)
    span = f"{p.since}..{p.until}"
    rows = _rows(span, counted)
    shown = rows[:MAX_ROWS]
    total = sum(r.amount.minor for r in rows)
    digest = hashlib.sha256(p.model_dump_json().encode()).hexdigest()[:12]
    return SpendForQueryOut(
        evidence_id=f"query:{span}:{digest}",
        since=p.since,
        until=p.until,
        total=_money(total),
        count=_count(led, p),
        merchants=shown,
        other=_money(total - sum(r.amount.minor for r in shown)),
        truncated=len(rows) > MAX_ROWS,
        not_counted=[r for r in _rows(span, near) if r.amount.minor > 0][:NOT_COUNTED],
        unmatched=[Data(data=s) for s in unmatched],
        plan=p,
    )


def _tops(p: QueryPlan) -> set[str]:
    """The top-level Categories a plan names, its own and those it narrows."""
    own = {c.partition(" > ")[0] for c in p.categories}
    return own | (_tops(p.within) if p.within else set())


def _rows(span: str, by: dict[int | None, tuple[str, int]]) -> list[CountedRow]:
    rows = [
        CountedRow(
            # never the name in an ID: it is bank text
            evidence_id=f"merchant:{span}:{mid if mid is not None else 'unknown'}",
            merchant_id=mid,
            merchant=Data(data=display_name(name)),
            amount=_money(minor),
        )
        for mid, (name, minor) in by.items()
        if minor
    ]
    return sorted(rows, key=lambda r: (-r.amount.minor, r.merchant.data))


def _count(led: Ledger, p: QueryPlan) -> int:
    """Spending Transactions in the range that the plan counts. shortcut: a
    Split counts by its Transaction's own Category, not its parts; count parts
    if Splits across counted and uncounted Categories become common."""
    start = date(int(p.since[:4]), int(p.since[5:]), 1)
    y, m = int(p.until[:4]), int(p.until[5:])
    end = date(y + m // 12, m % 12 + 1, 1)
    return sum(
        1
        for t in led.transactions()
        if start <= t.posted_on < end
        and p.account_id in (None, t.account_id)
        and (c := t.categorisation)
        and c.kind == "Spending"
        and c.category
        and p.counts(c.category[0], c.category[1], c.merchant_id)
    )


def spend_for_query(
    src: Source, inp: SpendForQueryIn, previous: QueryPlan | None = None
) -> SpendForQueryOut:
    p, unmatched = plan(src, inp, previous)
    return spend(src, p, unmatched)
