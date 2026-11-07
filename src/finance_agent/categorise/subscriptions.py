"""Subscription recognition (#104), after the registry (#103): known services
from their first charge, descriptor patterns asked about straight away, then
recurring detection (`categorise.recurring`). Every write goes through
`Ledger.record_detection`, which never overrides a user decision and writes
nothing when the evidence is unchanged, so re-running is idempotent.

Status and monthly cost (#105): read-only, derived from the registry and the
Merchant's charges each time, in exact pence, rounded once for display.
"""

import calendar
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from finance_agent.categorise.recurring import detect_recurring
from finance_agent.ledger import (
    NOT_SUBSCRIPTION,
    REGISTRY_KINDS,
    Ledger,
    SubscriptionEntry,
    Transaction,
)

log = logging.getLogger(__name__)  # counts only

SUB, BILL = "subscription", "bill"

# Generic UK and global services: name -> (kind, taxonomy v2 group). The single
# backend source; Manage links (#97) stay in the web app until they read this.
KNOWN_SERVICES: dict[str, tuple[str, str]] = {
    "Netflix": (SUB, "Streaming"),
    "Spotify": (SUB, "Streaming"),
    "Disney Plus": (SUB, "Streaming"),
    "Amazon Prime": (SUB, "Streaming"),
    "Prime Video": (SUB, "Streaming"),
    "Apple Music": (SUB, "Streaming"),
    "Apple TV": (SUB, "Streaming"),
    "YouTube Premium": (SUB, "Streaming"),
    "NOW TV": (SUB, "Streaming"),
    "Paramount Plus": (SUB, "Streaming"),
    "Deezer": (SUB, "Streaming"),
    "Tidal": (SUB, "Streaming"),
    "Audible": (SUB, "Streaming"),
    "Microsoft 365": (SUB, "Software"),
    "Adobe": (SUB, "Software"),
    "GitHub": (SUB, "Software"),
    "1Password": (SUB, "Software"),
    "Notion": (SUB, "Software"),
    "Canva": (SUB, "Software"),
    "Duolingo": (SUB, "Software"),
    "ChatGPT": (SUB, "AI tools"),
    "OpenAI": (SUB, "AI tools"),
    "Anthropic": (SUB, "AI tools"),
    "Midjourney": (SUB, "AI tools"),
    "PureGym": (SUB, "Memberships"),
    "The Gym Group": (SUB, "Memberships"),
    "The Economist": (SUB, "Memberships"),
    "iCloud": (SUB, "Cloud storage"),
    "Google One": (SUB, "Cloud storage"),
    "Dropbox": (SUB, "Cloud storage"),
    "Xbox Game Pass": (SUB, "Other subscriptions"),
    "PlayStation Plus": (SUB, "Other subscriptions"),
    "Patreon": (SUB, "Other subscriptions"),
    "Octopus Energy": (BILL, "Energy"),
    "British Gas": (BILL, "Energy"),
    "EDF Energy": (BILL, "Energy"),
    "OVO Energy": (BILL, "Energy"),
    "Thames Water": (BILL, "Water"),
    "Vodafone": (BILL, "Phone & broadband"),
    "O2": (BILL, "Phone & broadband"),
    "Three": (BILL, "Phone & broadband"),
    "EE": (BILL, "Phone & broadband"),
    "BT": (BILL, "Phone & broadband"),
    "Sky": (BILL, "Phone & broadband"),
    "Virgin Media": (BILL, "Phone & broadband"),
    "giffgaff": (BILL, "Phone & broadband"),
    "TV Licence": (BILL, "Other bills"),
}


def _key(name: str) -> str:
    # only spacing and a web suffix are ignored: other punctuation stays, so
    # "B & T" or "E-E" (another business's initials) never reads as BT or EE
    return re.sub(r"\.CO(M|\.UK)$", "", " ".join(name.upper().split()))


_BY_KEY = {k: n for n in KNOWN_SERVICES for k in (_key(n), _key(n).replace(" ", ""))}


def known_service(merchant: str) -> tuple[str, str, str] | None:
    """(service, kind, group) when the Merchant name is a known service and
    nothing else: "NETFLIX.COM" is, "NETFLIX FAN SHOP" is not. Non-ASCII
    names fail closed, so a lookalike letter cannot be stripped into a match.
    shortcut: whole-name match only; add aliases if real descriptors carry tails."""
    if not merchant.isascii():
        return None
    name = _BY_KEY.get(_key(merchant))
    return (name, *KNOWN_SERVICES[name]) if name else None


# Descriptor patterns that ask "Is this a subscription?": (pattern, group)
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bMEMBERSHIP\b"), "Memberships"),
    (re.compile(r"\bSUBSCRIPTIONS?\b|\bSUBSCR\b"), "Other subscriptions"),
    (re.compile(r"\bAPPLE\.COM/BILL\b|\bGOOGLE \*?PLAY\b"), "Other subscriptions"),
)


def pattern_group(description: str) -> str | None:
    """The Subscriptions group a raw descriptor's pattern suggests, if any."""
    if not description.isascii():
        return None
    d = description.upper()
    return next((g for p, g in PATTERNS if p.search(d)), None)


_KIND_OF_TOP = {top: kind for kind, top in REGISTRY_KINDS.items()}


def _merchant_spending(led: Ledger) -> dict[int, list[Transaction]]:
    by_merchant: dict[int, list[Transaction]] = {}
    for t in led.transactions():
        c = t.categorisation
        if c and c.kind == "Spending" and c.merchant_id is not None:
            by_merchant.setdefault(c.merchant_id, []).append(t)
    return by_merchant


def recognise(led: Ledger) -> int:
    """Record a detection for every Merchant whose Spending is recognised;
    returns how many registry entries were written."""
    by_merchant = _merchant_spending(led)
    written = 0
    for merchant_id, ts in sorted(by_merchant.items()):
        c = ts[0].categorisation
        if c is None or c.merchant is None:  # filtered above; for the type checker
            continue
        rec = detect_recurring((t.posted_on, int(-t.amount * 100)) for t in ts)
        known = known_service(c.merchant)
        group = next((g for t in ts if (g := pattern_group(t.description))), None)
        if known:
            _, kind, group = known
            reason, status = "known_service", "active"
        elif group:
            kind, reason, status = SUB, "pattern", "suggested"
        elif rec:
            top, leaf = c.category or ("", "")
            kind = _KIND_OF_TOP.get(top, SUB)
            group = leaf if top in _KIND_OF_TOP else "Other subscriptions"
            reason, status = "pattern", "suggested"
        else:
            continue
        written += led.record_detection(
            merchant_id,
            kind,
            group,
            reason,
            status,
            max(t.posted_on for t in ts),
            rec.charge_count if rec else None,
            rec.interval if rec else None,
        )
    log.info("subscriptions_recognised written=%d", written)
    return written


# --- Status and monthly cost (#105) ------------------------------------------

GRACE_DAYS = {"weekly": 2, "monthly": 5, "annual": 15}
COUNTED = ("active", "overdue")  # lapsed, cancelled and suggested never count


@dataclass(frozen=True)
class Charge:
    merchant_id: int
    posted_on: date
    minor: int  # positive, money out, in `currency`'s minor units
    currency: str = "GBP"  # anything else is a charge still needing an FX rate


@dataclass(frozen=True)
class SubscriptionCost:
    entry: SubscriptionEntry
    status: str  # active, overdue, lapsed, cancelled or suggested
    interval: str | None
    last_charge_on: date | None
    monthly: Fraction | None  # exact GBP pence a month; round only with `display`
    cost_note: str | None  # rate_needed, interval_needed or no_charge, when no cost
    counted: bool  # in the totals


@dataclass(frozen=True)
class CostSummary:
    items: list[SubscriptionCost]
    kind_totals: dict[str, Fraction]  # kind -> exact pence a month
    group_totals: dict[tuple[str, str], Fraction]  # (kind, group) -> exact pence
    rate_needed: int  # listed items left out of totals for want of an FX rate


def display(pence: Fraction) -> int:
    """Whole pence, rounded half-even: the only rounding step."""
    return round(pence)


def _due(last: date, interval: str, n: int) -> date:
    """The n-th charge expected after `last`; months clamp to the month end."""
    if interval == "weekly":
        return last + timedelta(weeks=n)
    months = last.month - 1 + n * (12 if interval == "annual" else 1)
    y, m = last.year + months // 12, months % 12 + 1
    return date(y, m, min(last.day, calendar.monthrange(y, m)[1]))


def _monthly(minor: int, interval: str) -> Fraction:
    if interval == "weekly":
        return Fraction(minor * 52, 12)
    return Fraction(minor, 12) if interval == "annual" else Fraction(minor)


def _cost(e: SubscriptionEntry, charges: list[Charge], today: date) -> SubscriptionCost:
    last = max(charges, key=lambda c: (c.posted_on, c.currency, c.minor), default=None)
    rec = detect_recurring(((c.posted_on, c.minor) for c in charges), min_charges=2)
    interval = e.interval or (rec.interval if rec else None)
    status = {"user_cancelled": "cancelled", "suggested": "suggested"}.get(
        e.status, "active"
    )
    if status == "active" and last and interval:
        grace = timedelta(GRACE_DAYS[interval])
        if today > _due(last.posted_on, interval, 2) + grace:
            status = "lapsed"
        elif today > _due(last.posted_on, interval, 1) + grace:
            status = "overdue"
    monthly, note = None, None
    if last is None:
        note = "no_charge"
    elif last.currency != "GBP":
        note = "rate_needed"  # the latest charge had no FX rate: never guessed
    elif interval is None:
        note = "interval_needed"
    else:
        monthly = _monthly(last.minor, interval)
    return SubscriptionCost(
        e,
        status,
        interval,
        last.posted_on if last else None,
        monthly,
        note,
        status in COUNTED and monthly is not None,
    )


def subscription_costs(
    entries: Iterable[SubscriptionEntry], charges: Iterable[Charge], today: date
) -> CostSummary:
    """Each listed entry's status and monthly cost, with totals that are the
    exact sums of the counted items. Negative entries are left out. The result
    does not depend on the order of `entries` or `charges`."""
    by_merchant: dict[int, list[Charge]] = {}
    for c in charges:
        by_merchant.setdefault(c.merchant_id, []).append(c)
    items = sorted(
        (
            _cost(e, by_merchant.get(e.merchant_id, []), today)
            for e in entries
            if e.status != NOT_SUBSCRIPTION and e.kind and e.group
        ),
        key=lambda x: (x.entry.merchant, x.entry.merchant_id),
    )
    kinds: dict[str, Fraction] = {}
    groups: dict[tuple[str, str], Fraction] = {}
    for x in items:
        if x.counted and x.monthly is not None and x.entry.kind and x.entry.group:
            kinds[x.entry.kind] = kinds.get(x.entry.kind, Fraction(0)) + x.monthly
            g = (x.entry.kind, x.entry.group)
            groups[g] = groups.get(g, Fraction(0)) + x.monthly
    rate_needed = sum(x.cost_note == "rate_needed" for x in items)
    return CostSummary(items, kinds, groups, rate_needed)


def costs(led: Ledger, today: date) -> CostSummary:
    """`subscription_costs` over the Ledger's registry and Spending charges."""
    in_gbp = led.foreign_in_gbp()  # the Ledger's one rate choice (#156)
    charges: list[Charge] = []
    for m, ts in _merchant_spending(led).items():
        for t in (t for t in ts if t.amount < 0):  # refunds are not charges
            own = int(-t.amount * 100)
            gbp = own if t.currency == "GBP" else in_gbp.get(t.id)
            if gbp is None:  # rate needed: kept in its own currency, never guessed
                charges.append(Charge(m, t.posted_on, own, t.currency))
            else:  # a converted charge keeps its sign
                charges.append(Charge(m, t.posted_on, abs(gbp)))
    s = subscription_costs(led.subscriptions(), charges, today)
    counted = sum(x.counted for x in s.items)
    log.info("subscription_costs items=%d counted=%d", len(s.items), counted)
    return s
