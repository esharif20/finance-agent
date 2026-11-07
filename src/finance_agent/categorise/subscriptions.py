"""Subscription recognition (#104), after the registry (#103): known services
from their first charge, descriptor patterns asked about straight away, then
recurring detection (`categorise.recurring`). Every write goes through
`Ledger.record_detection`, which never overrides a user decision and writes
nothing when the evidence is unchanged, so re-running is idempotent.
"""

import logging
import re

from finance_agent.categorise.recurring import detect_recurring
from finance_agent.ledger import REGISTRY_KINDS, Ledger, Transaction

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


def recognise(led: Ledger) -> int:
    """Record a detection for every Merchant whose Spending is recognised;
    returns how many registry entries were written."""
    by_merchant: dict[int, list[Transaction]] = {}
    for t in led.transactions():
        c = t.categorisation
        if c and c.kind == "Spending" and c.merchant_id is not None:
            by_merchant.setdefault(c.merchant_id, []).append(t)
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
