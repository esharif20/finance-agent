"""Categorisation: the versioned taxonomy, Merchant normalisation and the
rule chain of ADR 0005 (Merchant default Category -> descriptor rules ->
Uncategorised). Corrections are a Ledger overlay applied on read, in front of
every rule (ADR 0001); ML comes later, after rules.
"""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection

from finance_agent.ledger import UNCATEGORISED as _NONE
from finance_agent.ledger import Ledger
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

TAXONOMY_VERSION = 1
UNCATEGORISED = (_NONE, _NONE)

# Two levels: top level -> subcategories (<= 8 each). Bump TAXONOMY_VERSION on change.
TAXONOMY: dict[str, tuple[str, ...]] = {
    "Food & Drink": ("Groceries", "Eating out", "Takeaway", "Coffee"),
    "Transport": ("Public transport", "Taxi", "Fuel", "Parking"),
    "Shopping": ("Online", "Clothing", "Electronics", "Household"),
    "Bills & Utilities": ("Energy", "Water", "Phone & internet", "Council tax"),
    "Subscriptions": ("Streaming", "Software", "Memberships"),
    "Housing": ("Rent", "Mortgage", "Maintenance"),
    "Health": ("Pharmacy", "Fitness", "Medical"),
    "Entertainment": ("Events", "Games", "Hobbies"),
    "Travel": ("Flights", "Hotels", "Holidays"),
    "Personal care": ("Hair & beauty", "Toiletries"),
    _NONE: (_NONE,),
}

# Merchant -> (descriptor pattern, default Category). Matched against the raw
# upper-case Bank description; first match wins.
MERCHANTS: dict[str, tuple[re.Pattern[str], tuple[str, str]]] = {
    "Tesco": (re.compile(r"^TESCO\b"), ("Food & Drink", "Groceries")),
    "Pret A Manger": (re.compile(r"^PRET\b"), ("Food & Drink", "Coffee")),
    "Deliveroo": (re.compile(r"^DELIVEROO\b"), ("Food & Drink", "Takeaway")),
    "Amazon": (re.compile(r"^(AMZN|AMAZON|AMZ\*)"), ("Shopping", "Online")),
    "Spotify": (re.compile(r"\bSPOTIFY\b"), ("Subscriptions", "Streaming")),
    "TfL": (re.compile(r"^TFL\b"), ("Transport", "Public transport")),
}


def _upper(description: str) -> str:
    """ASCII-only upper case: str.upper() turns lookalikes such as the long s
    and dotless i into S and I, which would let them pass for a real Merchant."""
    return "".join(ch.upper() if ch.isascii() else ch for ch in description)


def merchant_for(description: str) -> str | None:
    """The seeded Merchant a raw Bank descriptor is an alias of, if any."""
    d = _upper(description)
    return next((m for m, (p, _) in MERCHANTS.items() if p.search(d)), None)


# Tokens that say where, not who. shortcut: a short list; extend from real exports.
LOCATIONS = frozenset({"GB", "GBR", "UK", "LONDON", "MANCHESTER", "BIRMINGHAM"})


def normalise(description: str) -> str:
    """The Merchant name for a descriptor: a seeded alias, else the descriptor
    without store numbers, payment references, locations and "REFUND".
    Deterministic, so the same shop maps to the same Merchant row on every
    import; "" when nothing is left to name."""
    known = merchant_for(description)
    if known is not None:
        return known
    words = [
        w
        for w in re.split(r"[\s*]+", _upper(description))
        if w and w != "REFUND" and w not in LOCATIONS and not re.search(r"\d", w)
    ]
    return " ".join(words)[:64]


# Descriptor rules for when no Merchant matches: (rule id, pattern, Category).
RULES: tuple[tuple[str, re.Pattern[str], tuple[str, str]], ...] = (
    ("parking", re.compile(r"\bPARKING\b"), ("Transport", "Parking")),
    ("pharmacy", re.compile(r"\bPHARMACY\b"), ("Health", "Pharmacy")),
    ("taxi", re.compile(r"\b(UBER|TAXI|CABS?)\b"), ("Transport", "Taxi")),
    ("fuel", re.compile(r"\b(PETROL|FUEL)\b"), ("Transport", "Fuel")),
    ("rent", re.compile(r"\bRENT\b"), ("Housing", "Rent")),
    (
        "council-tax",
        re.compile(r"\bCOUNCIL TAX\b"),
        ("Bills & Utilities", "Council tax"),
    ),
    ("gym", re.compile(r"\b(GYM|FITNESS)\b"), ("Health", "Fitness")),
)
FEE_TYPES = frozenset({"FEE", "CHG"})  # Lloyds Transaction Types for bank charges
REFUND = re.compile(r"\bREFUND\b")


@dataclass(frozen=True)
class Classification:
    kind: str  # Spending, Income, Transfer, Refund or Fee
    merchant: str | None  # a Merchant name from `normalise`
    category: tuple[str, str] | None  # (top level, subcategory)
    # how it was decided: "merchant:<name>", "rule:<id>" or "default"; the Ledger
    # reports "correction" when a user Correction overrides it
    source: str


SEED_DEFAULTS: dict[str, tuple[str, str] | None] = {
    m: cat for m, (_, cat) in MERCHANTS.items()
}


def classify(
    description: str,
    amount: int | Decimal,
    bank_type: str,
    *,
    transfer: bool,
    defaults: Mapping[str, tuple[str, str] | None] = SEED_DEFAULTS,
) -> Classification:
    """Kind first; then, for Spending and Refunds only, Merchant and Category:
    the Merchant's default (`defaults`, from the merchant table), then
    descriptor rules, then Uncategorised.

    `transfer` comes from the Ledger's Transfer links; Kind is never the Bank's
    Transaction Type (a TFR row to someone else is Spending).
    """
    d = _upper(description)
    if transfer:
        return Classification("Transfer", None, None, "rule:transfer")
    if bank_type.upper() in FEE_TYPES:
        return Classification("Fee", None, None, "rule:fee")
    if amount > 0 and merchant_for(d) is None and not REFUND.search(d):
        return Classification("Income", None, None, "default")
    kind = "Spending" if amount <= 0 else "Refund"
    merchant = normalise(d) or None
    category = defaults.get(merchant) if merchant else None
    if merchant and category:
        return Classification(kind, merchant, category, f"merchant:{merchant}")
    for rule_id, pattern, category in RULES:
        if pattern.search(d):
            return Classification(kind, merchant, category, f"rule:{rule_id}")
    return Classification(kind, merchant, UNCATEGORISED, "default")


def _seed_taxonomy(db: Scoped) -> dict[tuple[str, str], int]:
    """This user's Category ids for TAXONOMY_VERSION, inserting them once."""
    cat = schema.category
    rows = db.execute(
        db.select(cat, cat.c.id, cat.c.parent_id, cat.c.name).where(
            cat.c.taxonomy_version == TAXONOMY_VERSION
        )
    ).all()
    if not rows:
        for top, subs in TAXONOMY.items():
            parent = db.insert(cat, name=top, taxonomy_version=TAXONOMY_VERSION)
            for sub in subs:
                db.insert(
                    cat, name=sub, parent_id=parent, taxonomy_version=TAXONOMY_VERSION
                )
        return _seed_taxonomy(db)
    names = {r.id: r.name for r in rows}
    return {(names[r.parent_id], r.name): r.id for r in rows if r.parent_id}


def run(conn: Connection, user_id: int) -> int:
    """(Re)categorise every Transaction of this user; returns how many.

    Derived data: recomputed in full so new Transfer links, rules or taxonomy
    versions apply to the whole history. shortcut: full recompute per import;
    make it incremental by ledger_version if it gets slow.
    """
    db = Scoped(conn, user_id)
    ledger = Ledger(conn, user_id)
    ids = _seed_taxonomy(db)
    merchant_ids = {m.name: m.id for m in ledger.merchants()}
    for name, (_, category) in MERCHANTS.items():
        if name not in merchant_ids:
            merchant_ids[name] = db.insert(
                schema.merchant, name=name, default_category_id=ids[category]
            )
    # the merchant table is the source of defaults; one from a retired taxonomy
    # version no longer names a current Category, so rules decide instead
    defaults = {
        m.name: m.default_category
        for m in ledger.merchants()
        if m.default_category in ids
    }
    db.execute(db.delete(schema.categorisation))
    txns = ledger.transactions()
    for t in txns:
        c = classify(
            t.description,
            t.amount,
            t.bank_type,
            transfer=t.transfer_with is not None,
            defaults=defaults,
        )
        if c.merchant and c.merchant not in merchant_ids:
            merchant_ids[c.merchant] = db.insert(schema.merchant, name=c.merchant)
        db.insert(
            schema.categorisation,
            transaction_id=t.id,
            kind=c.kind,
            merchant_id=merchant_ids[c.merchant] if c.merchant else None,
            category_id=ids[c.category] if c.category else None,
            source=c.source,
            taxonomy_version=TAXONOMY_VERSION,
        )
    log.info("categorise transactions=%d", len(txns))
    return len(txns)
