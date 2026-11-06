"""Categorisation: the versioned taxonomy, Merchant normalisation and the
rule chain of ADR 0005 (Merchant default Category -> descriptor rules ->
Uncategorised). Corrections (#10) and ML come later, in front of / after rules.
"""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Connection

from finance_agent.ledger import Ledger
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

TAXONOMY_VERSION = 1
UNCATEGORISED = ("Uncategorised", "Uncategorised")

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
    "Uncategorised": ("Uncategorised",),
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


def merchant_for(description: str) -> str | None:
    """The normalised Merchant for a raw Bank descriptor, if one is known."""
    d = description.upper()
    return next((m for m, (p, _) in MERCHANTS.items() if p.search(d)), None)


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
    merchant: str | None
    category: tuple[str, str] | None  # (top level, subcategory)
    source: str  # "merchant:<name>", "rule:<id>", "none"; "" when no Category


def classify(
    description: str, amount: int | Decimal, bank_type: str, *, transfer: bool
) -> Classification:
    """Kind first; then, for Spending and Refunds only, Merchant and Category.

    `transfer` comes from the Ledger's Transfer links; Kind is never the Bank's
    Transaction Type (a TFR row to someone else is Spending).
    """
    d = description.upper()
    if transfer:
        return Classification("Transfer", None, None, "")
    if bank_type.upper() in FEE_TYPES:
        return Classification("Fee", None, None, "")
    merchant = merchant_for(d)
    if amount > 0 and merchant is None and not REFUND.search(d):
        return Classification("Income", None, None, "")
    kind = "Spending" if amount <= 0 else "Refund"
    if merchant is not None:
        category = MERCHANTS[merchant][1]
        return Classification(kind, merchant, category, f"merchant:{merchant}")
    for rule_id, pattern, category in RULES:
        if pattern.search(d):
            return Classification(kind, None, category, f"rule:{rule_id}")
    return Classification(kind, None, UNCATEGORISED, "none")


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
    ids = _seed_taxonomy(db)
    db.execute(db.delete(schema.categorisation))
    txns = Ledger(conn, user_id).transactions()
    for t in txns:
        c = classify(
            t.description, t.amount, t.bank_type, transfer=t.transfer_with is not None
        )
        db.insert(
            schema.categorisation,
            transaction_id=t.id,
            kind=c.kind,
            merchant=c.merchant,
            category_id=ids[c.category] if c.category else None,
            source=c.source,
            taxonomy_version=TAXONOMY_VERSION,
        )
    log.info("categorise transactions=%d", len(txns))
    return len(txns)
