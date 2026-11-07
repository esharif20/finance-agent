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
from bisect import insort
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from fractions import Fraction
from typing import Literal

from finance_agent.categorise.recurring import (
    CADENCES,
    TOLERANCE_PCT,
    detect_recurring,
)
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
    gbp: dict[int, list[tuple[date, int]]] = {}
    for ch in _charges(led):  # a charge still needing a rate is not comparable
        if ch.currency == "GBP":
            gbp.setdefault(ch.merchant_id, []).append((ch.posted_on, ch.minor))
    written = 0
    for merchant_id, ts in sorted(by_merchant.items()):
        c = ts[0].categorisation
        if c is None or c.merchant is None:  # filtered above; for the type checker
            continue
        rec = detect_recurring(gbp.get(merchant_id, []))
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
RECENT_DAYS = 365  # a price rise or trial conversion older than this is no flag
TRIAL_MAX_MINOR = 100  # a first charge of £1 or less from a trial service
# shortcut: usual free-trial days of the known services that run one, from
# their public offers; replace with Mail renewal signals when #80 lands
TRIAL_DAYS: dict[str, int] = {
    "Spotify": 30, "Amazon Prime": 30, "Prime Video": 30, "Apple Music": 30,
    "Apple TV": 7, "YouTube Premium": 30, "NOW TV": 7, "Paramount Plus": 7,
    "Deezer": 30, "Tidal": 30, "Audible": 30, "Microsoft 365": 30, "Adobe": 7,
    "Canva": 30, "Duolingo": 14, "1Password": 14, "Google One": 30,
    "Dropbox": 30, "Xbox Game Pass": 14,
}  # fmt: skip


@dataclass(frozen=True)
class Charge:
    merchant_id: int
    posted_on: date
    minor: int  # positive, money out, in `currency`'s minor units
    currency: str = "GBP"  # anything else is a charge still needing an FX rate
    account_id: int = 0


FlagCode = Literal[
    "overlap", "duplicate", "charged_after_cancel", "trial_converted", "price_rise"
]


@dataclass(frozen=True)
class Flag:
    """A waste flag (#106): overlap, duplicate, charged_after_cancel,
    trial_converted or price_rise."""

    code: FlagCode
    on: date | None = None  # the charge that raised it, when one did
    from_minor: int | None = None  # price_rise: the previous and the new GBP price
    to_minor: int | None = None


@dataclass(frozen=True)
class SubscriptionCost:
    entry: SubscriptionEntry
    status: str  # active, overdue, lapsed, cancelled or suggested
    interval: str | None
    last_charge_on: date | None
    monthly: Fraction | None  # exact GBP pence a month; round only with `display`
    cost_note: str | None  # rate_needed, interval_needed or no_charge, when no cost
    counted: bool  # in the totals
    flags: tuple[Flag, ...] = ()
    trial: "Trial | None" = None


@dataclass(frozen=True)
class Trial:
    """A free trial read from posted charges only: partial until Mail (#80)."""

    started_on: date  # the small first charge
    converts_on: date  # expected, from the service's usual trial length
    converted: bool  # a full charge has followed


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
    # only GBP amounts are comparable: a charge still needing a rate stays out
    gbp = sorted((c.posted_on, c.minor) for c in charges if c.currency == "GBP")
    rec = detect_recurring(gbp, min_charges=2)
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
    trial, converted_on = _trial(e, gbp)
    flags: list[Flag] = []
    if status == "cancelled" and e.kind == SUB:
        # shortcut: changed_at is the latest user change, so a regroup after the
        # cancel moves the date; read the cancel's own row if that matters
        after = [c.posted_on for c in charges if c.posted_on > e.changed_at.date()]
        if after:
            flags.append(Flag("charged_after_cancel", on=min(after)))
    if status in COUNTED:  # waste flags never fire on lapsed or cancelled items
        recent = today - timedelta(RECENT_DAYS)
        if e.kind == SUB and converted_on and converted_on > recent:
            flags.append(Flag("trial_converted", on=converted_on))
        rise = _price_rise(gbp[1:] if trial else gbp)  # leaving a trial is no rise
        if rise and rise.on and rise.on > recent:
            flags.append(rise)
    return SubscriptionCost(
        e,
        status,
        interval,
        last.posted_on if last else None,
        monthly,
        note,
        status in COUNTED and monthly is not None,
        tuple(flags),
        trial,
    )


def _trial(
    e: SubscriptionEntry, gbp: list[tuple[date, int]]
) -> tuple[Trial | None, date | None]:
    """A small first charge from a known service that runs trials starts one;
    the first full charge after it converts it. Best effort (partial)."""
    known = known_service(e.merchant)
    days = TRIAL_DAYS.get(known[0]) if known else None
    if not days or not gbp or gbp[0][1] > TRIAL_MAX_MINOR:
        return None, None
    started = gbp[0][0]
    full = next((d for d, a in gbp[1:] if a > TRIAL_MAX_MINOR), None)
    return Trial(started, started + timedelta(days), full is not None), full


def _price_rise(gbp: list[tuple[date, int]]) -> Flag | None:
    """The latest price against the previous one. Charges in date order form
    price runs: a charge beyond ±5% of its run's median starts a new run, so
    jitter is no rise and a one-off dearer charge is followed by a cut.
    Pure and local on purpose; #148's engine detector may converge on it."""
    runs: list[tuple[date, list[int]]] = []
    for on, minor in gbp:
        if runs and _close(minor, _mid(runs[-1][1])):
            insort(runs[-1][1], minor)
        else:
            runs.append((on, [minor]))
    if len(runs) < 2:
        return None
    before, now = _mid(runs[-2][1]), _mid(runs[-1][1])
    return Flag("price_rise", runs[-1][0], before, now) if now > before else None


def _mid(sorted_minor: list[int]) -> int:
    return sorted_minor[len(sorted_minor) // 2]  # upper median, as recurring's


def _close(minor: int, median: int) -> bool:
    return abs(minor - median) * 100 <= median * TOLERANCE_PCT


def subscription_costs(
    entries: Iterable[SubscriptionEntry], charges: Iterable[Charge], today: date
) -> CostSummary:
    """Each listed entry's status, monthly cost, waste flags and trial, with
    totals that are the exact sums of the counted items. Negative entries are
    left out. The result does not depend on the order of `entries` or `charges`."""
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
    items = _shared_flags(items, by_merchant)
    rate_needed = sum(x.cost_note == "rate_needed" for x in items)
    return CostSummary(items, kinds, groups, rate_needed)


def _shared_flags(
    items: list[SubscriptionCost], by_merchant: dict[int, list[Charge]]
) -> list[SubscriptionCost]:
    """Overlap: two or more counted Subscriptions in one group. Duplicate: one
    service (a Merchant, or Merchants naming one known service) charged from
    two or more Accounts within its shortest cadence gap, so a card switched a
    period ago is not one. Neither fires on lapsed or cancelled items."""
    subs = [x for x in items if x.status in COUNTED and x.entry.kind == SUB]
    in_group = Counter(x.entry.group for x in subs)
    service: dict[int, str | int] = {}
    pooled: dict[str | int, list[Charge]] = {}
    window: dict[str | int, int] = {}
    for x in subs:
        known = known_service(x.entry.merchant)
        k = service[x.entry.merchant_id] = known[0] if known else x.entry.merchant_id
        pooled.setdefault(k, []).extend(by_merchant.get(x.entry.merchant_id, []))
        lo = CADENCES[x.interval or "monthly"][0]
        window[k] = min(window.get(k, lo), lo)
    duplicate: set[str | int] = set()
    for k, cs in pooled.items():
        if cs:
            last = max(c.posted_on for c in cs)
            near = {c.account_id for c in cs if (last - c.posted_on).days < window[k]}
            if len(near) > 1:
                duplicate.add(k)

    def more(x: SubscriptionCost) -> SubscriptionCost:
        k = service.get(x.entry.merchant_id)
        if k is None:
            return x
        new = [Flag("overlap")] * (in_group[x.entry.group] > 1)
        new += [Flag("duplicate")] * (k in duplicate)
        return replace(x, flags=(*x.flags, *new))

    return [more(x) for x in items]


def _charges(led: Ledger) -> list[Charge]:
    """Every Spending charge in exact minor units, in GBP through the Ledger's
    one FX rate (#156), or in its own currency while a rate is needed."""
    in_gbp = led.foreign_in_gbp()
    charges: list[Charge] = []
    for m, ts in _merchant_spending(led).items():
        for t in (t for t in ts if t.amount < 0):  # refunds are not charges
            own = int((-t.amount).scaleb(2))  # exact: amounts are whole minor units
            gbp = own if t.currency == "GBP" else in_gbp.get(t.id)
            if gbp is None:  # rate needed: kept in its own currency, never guessed
                charges.append(Charge(m, t.posted_on, own, t.currency, t.account_id))
            else:  # a converted charge keeps its sign
                charges.append(Charge(m, t.posted_on, abs(gbp), "GBP", t.account_id))
    return charges


def costs(led: Ledger, today: date) -> CostSummary:
    """`subscription_costs` over the Ledger's registry and Spending charges."""
    s = subscription_costs(led.subscriptions(), _charges(led), today)
    counted = sum(x.counted for x in s.items)
    flagged = sum(bool(x.flags) for x in s.items)
    log.info(
        "subscription_costs items=%d counted=%d flagged=%d",
        len(s.items),
        counted,
        flagged,
    )
    return s
