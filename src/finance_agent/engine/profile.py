"""The L0 profile (#327 part 1): what the Assistant always knows about the user's
money, so a question needs no search to start from.

`build` reads it from the Engine and the Ledger alone, every figure in minor
units: Accounts with balances, last-row dates and a below-zero flag; recurring
income and the next payday (`forecast.cash_flow`); bills and subscriptions
with their monthly cost (`subscriptions.costs`); each Category's median month
over the recent quarter and the past year of complete months; rising or
falling Categories; unusual recent payments; goals and Memories (ids and tags
only, never their text); and the history window.

`render` turns it into compact, deterministic text (about 800 tokens or fewer,
fixed order, so a model's KV cache reuses it). Its figures are exactly the
Profile's: `assistant.values.tool_values("profile", profile)` turns the
Profile into grounding values (ADR 0007), and the text holds no other digits.

`profile_context(conn, user_id)` is the entry point for the prompt. It keeps
one Profile per user in memory only, keyed by the ledger version, the day and
the Memories, so a stale one is never served: a sync or a Correction bumps
the version and the next read rebuilds it. Nothing is written to disk. The
profile never goes to a log: only counts and timings do.
"""

import calendar
import logging
import threading
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection, Engine

from finance_agent.categorise.display_names import display_name
from finance_agent.categorise.subscriptions import costs, display
from finance_agent.engine import forecast
from finance_agent.engine.baselines import median
from finance_agent.ledger import Ledger, Transaction
from finance_agent.ledger.proposals import Proposals

log = logging.getLogger(__name__)  # counts and timings only

VERSION = 1  # bump when what a Profile holds or how it renders changes
GBP = "GBP"
LONDON = ZoneInfo("Europe/London")
RECENT_DAYS = 30  # "recent" for unusual payments
UNUSUAL_FLOOR = 5_000  # pence: smaller payments are never unusual
UNUSUAL_TIMES = 3  # times the Merchant's median earlier payment
UNUSUAL_FIRST = 10_000  # pence: a first payment to a Merchant this large
TREND_MIN = 2_000  # pence between the quarter's and the year's median
TREND_MONTHS = 6  # complete months before a trend is called
LISTED = ("active", "overdue", "suggested")  # cost statuses worth knowing
MAX_ACCOUNTS = 8
MAX_INCOME = 3
MAX_BILLS = 12
MAX_CATEGORIES = 10
MAX_TRENDS = 5
MAX_UNUSUAL = 5
MAX_MEMORIES = 8

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class Amount(BaseModel):
    """Money as the tools give it: {"minor", "currency"} grounds as Money."""

    model_config = _FROZEN
    minor: int
    currency: str = GBP


class AccountFact(BaseModel):
    model_config = _FROZEN
    account_id: int
    name: str
    balance: Amount | None  # None when the Bank gives no balance
    as_of: date | None  # the Account's latest Transaction
    overdrawn: bool  # balance below zero


class IncomeFact(BaseModel):
    model_config = _FROZEN
    name: str  # the payer, as the Bank describes it
    interval: str
    amount: Amount  # expected in the as-of month
    next_on: date | None  # the next payday on or after the as-of day


class BillFact(BaseModel):
    model_config = _FROZEN
    merchant_id: int
    name: str
    kind: str | None  # "subscription" or "bill"
    status: str  # active, overdue or suggested ("likely")
    interval: str | None
    monthly: Amount


class KindTotal(BaseModel):
    model_config = _FROZEN
    name: str  # the kind
    monthly: Amount  # confirmed items only, as `fa subs` totals them


class CategoryFact(BaseModel):
    model_config = _FROZEN
    name: str
    median_3m: Amount
    median_12m: Amount


class TrendFact(BaseModel):
    model_config = _FROZEN
    name: str
    direction: Literal["rising", "falling"]
    median_3m: Amount
    median_12m: Amount


class PaymentFact(BaseModel):
    model_config = _FROZEN
    transaction_id: int
    name: str
    posted_on: date
    amount: Amount  # positive: money out


class MemoryFact(BaseModel):
    model_config = _FROZEN
    memory_id: int
    type: str
    label: str  # the Memory's tags, never its text


class Profile(BaseModel):
    """Lists, not tuples: `tool_values` walks lists."""

    model_config = _FROZEN
    evidence_id: str  # "profile:<VERSION>:<ledger version>"
    as_of: date
    first_month: str | None
    last_month: str | None
    months: int
    accounts: list[AccountFact] = []
    income: list[IncomeFact] = []
    bills: list[BillFact] = []
    totals: list[KindTotal] = []
    categories: list[CategoryFact] = []
    trends: list[TrendFact] = []
    unusual: list[PaymentFact] = []
    goals: list[MemoryFact] = []
    memories: list[MemoryFact] = []


def _minor(x: Decimal) -> int:
    return int(x.scaleb(2))


def _ym(k: int) -> str:
    return f"{k // 12}-{k % 12 + 1:02d}"


def build(conn: Connection, user_id: int, today: date) -> Profile:
    """The Profile as of `today`, read in one transaction."""
    led = Ledger(conn, user_id)
    version = led.ledger_version()
    months = sorted(led.months())
    txns = led.transactions()

    last_on: dict[int, date] = {}
    for t in txns:
        last_on[t.account_id] = max(last_on.get(t.account_id, t.posted_on), t.posted_on)
    accounts: list[AccountFact] = []
    for a in led.accounts()[:MAX_ACCOUNTS]:
        raw = a.bank_balance if a.bank_balance is not None else a.ledger_balance
        bal = None if raw is None else Amount(minor=_minor(raw), currency=a.currency)
        accounts.append(
            AccountFact(
                account_id=a.id,
                name=a.name or a.masked_number,
                balance=bal,
                as_of=last_on.get(a.id),
                overdrawn=bal is not None and bal.minor < 0,
            )
        )

    flow = forecast.cash_flow(led, today, 1)
    income = [
        IncomeFact(
            name=r.merchant,
            interval=r.interval,
            amount=Amount(minor=r.months[0]),
            next_on=next((d for d in r.dates if d >= today), None),
        )
        for r in sorted(flow.income, key=lambda r: (-r.months[0], r.merchant))
    ][:MAX_INCOME]

    cost = costs(led, today)
    bills = sorted(
        (
            BillFact(
                merchant_id=i.entry.merchant_id,
                name=display_name(i.entry.merchant),
                kind=i.entry.kind,
                status=i.status,
                interval=i.interval,
                monthly=Amount(minor=display(i.monthly)),
            )
            for i in cost.items
            if i.status in LISTED and i.monthly is not None
        ),
        key=lambda b: (-b.monthly.minor, b.name),
    )[:MAX_BILLS]
    totals = [
        KindTotal(name=k, monthly=Amount(minor=display(v)))
        for k, v in sorted(cost.kind_totals.items())
    ]

    start = today.year * 12 + today.month - 1
    window = [_ym(k) for k in range(start - 12, start)]
    window = [m for m in window if months and m >= months[0]]
    spend: dict[str, list[int]] = {}
    for i, m in enumerate(window):
        for s in led.spending_by_category(m):
            spend.setdefault(s.name, [0] * len(window))[i] = _minor(s.amount)
    cats = sorted(
        (
            CategoryFact(
                name=n,
                median_3m=Amount(minor=median(v[-3:])),
                median_12m=Amount(minor=median(v)),
            )
            for n, v in spend.items()
        ),
        key=lambda c: (-c.median_12m.minor, c.name),
    )
    trends = [
        TrendFact(
            name=c.name, direction=d, median_3m=c.median_3m, median_12m=c.median_12m
        )
        for c in cats
        if len(window) >= TREND_MONTHS and (d := _direction(c)) is not None
    ]
    trends.sort(key=lambda t: (-abs(t.median_3m.minor - t.median_12m.minor), t.name))

    mems = Proposals(conn, user_id).memories()  # newest first

    def facts(goal: bool) -> list[MemoryFact]:
        return [
            MemoryFact(memory_id=m.id, type=m.type, label=", ".join(m.tags) or m.type)
            for m in mems
            if (m.type == "goal") == goal
        ][:MAX_MEMORIES]

    return Profile(
        evidence_id=f"profile:{VERSION}:{version}",
        as_of=today,
        first_month=months[0] if months else None,
        last_month=months[-1] if months else None,
        months=len(months),
        accounts=accounts,
        income=income,
        bills=bills,
        totals=totals,
        categories=[c for c in cats if c.median_12m.minor or c.median_3m.minor][
            :MAX_CATEGORIES
        ],
        trends=trends[:MAX_TRENDS],
        unusual=_unusual(forecast.gbp_rows(txns, led.foreign_in_gbp()), today),
        goals=facts(True),
        memories=facts(False),
    )


def _direction(c: CategoryFact) -> Literal["rising", "falling"] | None:
    m3, m12 = c.median_3m.minor, c.median_12m.minor
    if m3 - m12 >= TREND_MIN and 4 * m3 >= 5 * m12:  # up a quarter or more
        return "rising"
    if m12 - m3 >= TREND_MIN and 4 * m3 <= 3 * m12:  # down a quarter or more
        return "falling"
    return None


def _unusual(gbp: list[Transaction], today: date) -> list[PaymentFact]:
    """Spending in the last RECENT_DAYS that is UNUSUAL_TIMES the Merchant's
    median earlier payment (and at least UNUSUAL_FLOOR), or a first payment to
    a Merchant of UNUSUAL_FIRST or more; the largest MAX_UNUSUAL."""
    since = today - timedelta(days=RECENT_DAYS)
    paid: dict[int, list[tuple[date, int, int, str]]] = {}
    for t in gbp:
        c = t.categorisation
        if c is not None and c.kind == "Spending" and c.merchant_id is not None:
            row = (t.posted_on, t.id, -_minor(t.amount), c.merchant or "")
            paid.setdefault(c.merchant_id, []).append(row)
    found: list[PaymentFact] = []
    for rows in paid.values():
        rows.sort()
        for i, (on, tid, pence, name) in enumerate(rows):
            if not since < on <= today or pence <= 0:
                continue
            earlier = [p for _, _, p, _ in rows[:i]]
            odd = (
                pence >= UNUSUAL_FLOOR and pence >= UNUSUAL_TIMES * median(earlier)
                if earlier
                else pence >= UNUSUAL_FIRST
            )
            if odd:
                found.append(
                    PaymentFact(
                        transaction_id=tid,
                        name=display_name(name),
                        posted_on=on,
                        amount=Amount(minor=pence),
                    )
                )
    found.sort(key=lambda u: (-u.amount.minor, u.posted_on, u.transaction_id))
    return found[:MAX_UNUSUAL]


def money(a: Amount) -> str:
    """As the Assistant writes money: "£1,234.56", "-£5.00", "12.00 EUR"."""
    sign, units = ("-" if a.minor < 0 else ""), abs(a.minor)
    figure = f"{units // 100:,}.{units % 100:02d}"
    return f"{sign}£{figure}" if a.currency == GBP else f"{sign}{figure} {a.currency}"


def _spoken(month: str) -> str:
    """ "2025-11" as "November 2025", the form grounding reads as a month."""
    return f"{calendar.month_name[int(month[5:])]} {month[:4]}"


def render(p: Profile) -> str:
    """The Profile as prompt text: fixed order, no figure the Profile lacks."""
    head = f"Your money on {p.as_of.isoformat()}"
    if p.first_month and p.last_month:
        head += f", history {_spoken(p.first_month)} to {_spoken(p.last_month)}"
        head += f" ({p.months} months)"
    else:
        head += ", no history yet"
    lines = [head + ". Amounts in GBP unless marked."]

    def section(title: str, items: list[str]) -> None:
        if items:
            lines.append(f"{title}: " + "; ".join(items) + ".")

    section(
        "Accounts",
        [
            f"{a.name} {money(a.balance) if a.balance else 'no balance'}"
            + (" below zero" if a.overdrawn else "")
            + (f" as of {a.as_of.isoformat()}" if a.as_of else "")
            for a in p.accounts
        ],
    )
    section(
        "Income",
        [
            f"{i.name} {i.interval}, {money(i.amount)} this month"
            + (f", next on {i.next_on.isoformat()}" if i.next_on else "")
            for i in p.income
        ],
    )
    section(
        "Bills and subscriptions a month",
        [
            f"{b.name} {money(b.monthly)} ({b.kind or 'subscription'}"
            + {"suggested": ", likely", "overdue": ", overdue"}.get(b.status, "")
            + ")"
            for b in p.bills
        ],
    )
    section("Confirmed a month", [f"{t.name} {money(t.monthly)}" for t in p.totals])
    section(
        "Typical month by Category, median of the recent quarter / past year",
        [
            f"{c.name} {money(c.median_3m)} / {money(c.median_12m)}"
            for c in p.categories
        ],
    )
    section(
        "Trends",
        [
            f"{t.name} {t.direction}, {money(t.median_3m)} "
            f"against {money(t.median_12m)}"
            for t in p.trends
        ],
    )
    section(
        "Unusual recent payments",
        [f"{u.name} {money(u.amount)} on {u.posted_on.isoformat()}" for u in p.unusual],
    )
    section("Goals", [g.label for g in p.goals])
    section("Memories", [f"{m.type}: {m.label}" for m in p.memories])
    return "\n".join(lines)


type _Key = tuple[int, date, tuple[tuple[int, str, tuple[str, ...]], ...]]
_cache: dict[int, tuple[_Key, str, Profile]] = {}
_building = threading.Lock()


def _key(conn: Connection, user_id: int, today: date) -> _Key:
    mems = Proposals(conn, user_id).memories()
    return (
        Ledger(conn, user_id).ledger_version(),
        today,
        tuple((m.id, m.type, m.tags) for m in mems),
    )


def profile_context(
    conn: Connection, user_id: int, today: date | None = None
) -> tuple[str, Profile]:
    """(prompt text, Profile) for the Assistant's stable prefix: the cached
    Profile while the Ledger is unchanged, else a fresh build. Ground replies
    with `assistant.values.tool_values("profile", profile)`."""
    today = today or datetime.now(LONDON).date()
    key = _key(conn, user_id, today)
    hit = _cache.get(user_id)
    if hit is not None and hit[0] == key:
        return hit[1], hit[2]
    p = build(conn, user_id, today)
    text = render(p)
    _cache[user_id] = (key, text, p)
    return text, p


def forget(user_id: int | None = None) -> None:
    """Drop the cached Profile (all users when None)."""
    if user_id is None:
        _cache.clear()
    else:
        _cache.pop(user_id, None)


def refresh_quietly(engine: Engine, user_id: int) -> None:
    """Build (or confirm) the cached Profile; never raises, one at a time.
    The first scan when `fa` starts, and the refresh after each sync."""
    if not _building.acquire(blocking=False):
        return
    try:
        t0 = time.monotonic_ns()
        with engine.begin() as conn:
            _, p = profile_context(conn, user_id)
        ms = (time.monotonic_ns() - t0) // 1_000_000
        log.info("profile ready months=%d ms=%d", p.months, ms)
    except Exception as e:  # noqa: BLE001
        log.warning("profile refresh failed: %s", type(e).__name__)
    finally:
        _building.release()


def start(engine: Engine, user_id: int) -> threading.Thread:
    """`refresh_quietly` in a daemon thread, so a sync or startup never waits."""
    t = threading.Thread(
        target=refresh_quietly, args=(engine, user_id), name="profile", daemon=True
    )
    t.start()
    return t
