"""Categorisation: the versioned taxonomy, Merchant normalisation and the
rule chain of ADR 0005 (Merchant default Category -> descriptor rules ->
Uncategorised). Corrections are a Ledger overlay applied on read, in front of
every rule (ADR 0001); the embedding model comes last, gated (`auto`).
"""

import logging
import re
import tomllib
from collections import Counter
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Connection, func

from finance_agent.categorise import auto, subscriptions
from finance_agent.ledger import UNCATEGORISED as _NONE
from finance_agent.ledger import Ledger
from finance_agent.ledger.taxonomy import TAXONOMIES
from finance_agent.ledger.taxonomy import TAXONOMY as TAXONOMY  # re-exported
from finance_agent.ledger.taxonomy import TAXONOMY_VERSION as TAXONOMY_VERSION
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

UNCATEGORISED = (_NONE, _NONE)

type Category = tuple[str, str]


def _token(token: str) -> str:
    """One `match` token of merchants_uk.toml as a regex over `_squash`ed text:
    "^X" only at the start, "X$" only at the end, "X*" no word boundary after,
    else whole words."""
    token = token.strip()
    if token.endswith("$"):
        return _token(token[:-1]).removesuffix(r"(?![A-Z0-9])") + "$"
    start = token.startswith("^")
    open_end = token.endswith("*")
    words = token.removeprefix("^").removesuffix("*").split()
    body = r"\s+".join(re.escape(w) for w in words)
    return (
        ("^" if start else r"(?<![A-Z0-9])")
        + body
        + ("" if open_end else r"(?![A-Z0-9])")
    )


# What may follow a `whole` Merchant's name: web suffix, company or place
# words, reference numbers. "NETFLIX FAN SHOP" is not Netflix (see
# subscriptions.known_service), so the rest of the descriptor must be noise.
_TAIL = (
    r"(?:\.COM|\.CO\.UK|\.COM/BILL| (?:LTD|LIMITED|PLC|UK|GB|GBR|LONDON|CD)"
    r"| [A-Z]{0,3}\d[A-Z0-9-]*)*$"
)


def _category(path: str) -> Category:
    top, sub = path.split("/", 1)
    return top, sub


def _load_base() -> tuple[
    dict[str, tuple[re.Pattern[str], Category]],
    tuple[tuple[str, re.Pattern[str], Category], ...],
]:
    """merchants_uk.toml (#215): Merchants in precedence order, sub-brand
    groups before base groups, and the extra keyword rules."""
    data = tomllib.loads(_BASE_FILE.read_text(encoding="utf-8"))
    merchants: dict[str, tuple[re.Pattern[str], Category]] = {}
    for group in (*data["sub_brand"], *data["base"]):
        for m in group["merchants"]:
            tokens = m.get("match") or [m["name"].upper()]
            if m.get("whole"):  # the descriptor is the name and nothing else
                names = "|".join(
                    _token(t).removesuffix(r"(?![A-Z0-9])") for t in tokens
                )
                pattern = re.compile(f"^(?:PAYPAL )?(?:{names}){_TAIL}")
            else:
                pattern = re.compile("|".join(_token(t) for t in tokens))
            category = _category(m.get("category", group["category"]))
            merchants[m["name"]] = (pattern, category)
    rules = tuple(
        (r["id"], re.compile(r["pattern"]), _category(r["category"]))
        for r in data["rule"]
    )
    return merchants, rules


_BASE_FILE = Path(__file__).with_name("merchants_uk.toml")
# Merchant -> (descriptor pattern, default Category), matched against the
# `_squash`ed upper-case Bank description; first match wins, so the order is
# the precedence: sub-brands (TESCO PFS) before the retailer (TESCO).
MERCHANTS, _BASE_RULES = _load_base()


def _upper(description: str) -> str:
    """ASCII-only upper case: str.upper() turns lookalikes such as the long s
    and dotless i into S and I, which would let them pass for a real Merchant."""
    return "".join(ch.upper() if ch.isascii() else ch for ch in description)


# Leading tokens that name how a card paid, not who was paid (#310): wallets
# (WL *, GPAY, APPLEPAY, CRV*, CURVE) and card processors (SQ *, SP *, ZTL*...).
# A trailing GOOG is Google Pay's wallet tag ("WL *VUE*GOOG" is Vue).
_WALLETS = frozenset({"WL", "GPAY", "APPLEPAY", "CRV", "CURVE"})
PROCESSORS = {"SQ": "Square", "SP": "Shopify", "SUMUP": "SumUp", "ZTL": "Zettle",
              "IZ": "Zettle", "PAYPAL": "PayPal"}  # fmt: skip
# Stores whose descriptor names a product after the platform: "GOOGLE *Colab"
STORES = {"GOOGLE": "Google", "APPLE.COM/BILL": "Apple", "AMZN DIGITAL": "Amazon",
          "AMAZON DIGITAL": "Amazon"}  # fmt: skip
_STORE_SEEDS = frozenset({"Google", "Apple", "Amazon"})


def _bare(words: list[str]) -> list[str]:
    """Wallet and processor tokens dropped from the front, a wallet tag from
    the end. PAYPAL stays: its own patterns handle "PAYPAL *NETFLIX"."""
    while (
        len(words) > 1
        and words[0] != "PAYPAL"
        and (words[0] in _WALLETS or words[0] in PROCESSORS)
    ):
        words = words[1:]
    return words[:-1] if len(words) > 1 and words[-1] == "GOOG" else words


def _squash(upper: str) -> str:
    """Matching form: `*` and apostrophes dropped, spaces collapsed, so
    "SAINSBURY'S S/MKTS" and "AMAZON PRIME*AB12" meet one pattern."""
    plain = upper.replace("'", "").replace("’", "").replace("*", " ")
    return " ".join(_bare(plain.split()))


def platform(description: str) -> str | None:
    """The platform or processor a descriptor was paid through, if any:
    "GOOGLE *Colab" is Google, "SQ *SYNTH CAFE" is Square."""
    words = _upper(description).replace("*", " ").split()
    while len(words) > 1 and words[0] in _WALLETS:
        words = words[1:]
    text = " ".join(words)
    for name, p in (*STORES.items(), *PROCESSORS.items()):
        if text == name or text.startswith(name + " "):
            return p
    return None


def _seed(squashed: str) -> str | None:
    return next((m for m, (p, _) in MERCHANTS.items() if p.search(squashed)), None)


def _product(squashed: str) -> str | None:
    """The product after a store's name, when it names one (not only a ref)."""
    for name in STORES:
        if squashed.startswith(name + " "):
            rest = squashed[len(name) + 1 :]
            if any(not re.search(r"\d", w) for w in rest.split()):
                return rest
    return None


def merchant_for(description: str) -> str | None:
    """The seeded Merchant a raw Bank descriptor is an alias of, if any. A
    store's product is its own Merchant (#310): "GOOGLE *Google One" is Google
    One, and an unseeded product ("GOOGLE *CLOUD") is no seed, not Google."""
    d = _squash(_upper(description))
    if (rest := _product(d)) is not None:
        if found := _seed(rest):
            return found
        found = _seed(d)
        return None if found in _STORE_SEEDS else found
    return _seed(d)


# Tokens that say where, not who. shortcut: a short list; extend from real exports.
LOCATIONS = frozenset({"GB", "GBR", "UK", "LONDON", "MANCHESTER", "BIRMINGHAM"})


def normalise(description: str) -> str:
    """The Merchant name for a descriptor: a seeded alias, else the descriptor
    without wallet and processor prefixes, store numbers, payment references,
    locations and "REFUND". Deterministic, so the same shop maps to the same
    Merchant row on every import; "" when nothing is left to name."""
    known = merchant_for(description)
    if known is not None:
        return known
    words = [
        w
        for w in _bare([w for w in re.split(r"[\s*]+", _upper(description)) if w])
        if w != "REFUND" and w not in LOCATIONS and not re.search(r"\d", w)
    ]
    return " ".join(words)[:64]


# Descriptor rules for when no Merchant matches: (rule id, pattern, Category).
RULES: tuple[tuple[str, re.Pattern[str], tuple[str, str]], ...] = (
    ("parking", re.compile(r"\bPARKING\b"), ("Transport", "Parking")),
    ("pharmacy", re.compile(r"\bPHARMACY\b"), ("Health", "Pharmacy")),
    ("taxi", re.compile(r"\b(UBER|TAXI|CABS?)\b"), ("Transport", "Taxi")),
    ("fuel", re.compile(r"\b(PETROL|FUEL)\b"), ("Transport", "Fuel")),
    ("rent", re.compile(r"\bRENT\b"), ("Bills", "Rent or mortgage")),
    (
        "council-tax",
        re.compile(r"\bCOUNCIL TAX\b"),
        ("Bills", "Council tax"),
    ),
    ("gym", re.compile(r"\b(GYM|FITNESS)\b"), ("Health", "Fitness")),
    *_BASE_RULES,
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
    ask: Collection[str] = (),
) -> Classification:
    """Kind first; then, for Spending and Refunds only, Merchant and Category:
    the Merchant's default (`defaults`, from the merchant table), then
    descriptor rules, then Uncategorised.

    `transfer` comes from the Ledger's Transfer links; Kind is never the Bank's
    Transaction Type (a TFR row to someone else is Spending). For a Merchant in
    `ask` ("Always ask", #125) it abstains: no default, rule or guess.
    """
    d = _upper(description)
    if transfer:
        return Classification("Transfer", None, None, "rule:transfer")
    if bank_type.upper() in FEE_TYPES:
        return Classification("Fee", None, None, "rule:fee")
    if amount > 0 and _seed(_squash(d)) is None and not REFUND.search(d):
        return Classification("Income", None, None, "default")
    kind = "Spending" if amount <= 0 else "Refund"
    merchant = normalise(d) or None
    if merchant in ask:
        return Classification(kind, merchant, None, "always_ask")
    category = defaults.get(merchant) if merchant else None
    if merchant and category:
        return Classification(kind, merchant, category, f"merchant:{merchant}")
    for rule_id, pattern, category in RULES:
        if pattern.search(d):
            return Classification(kind, merchant, category, f"rule:{rule_id}")
    return Classification(kind, merchant, UNCATEGORISED, "default")


def seed_taxonomy(
    conn: Connection, user_id: int, version: int = TAXONOMY_VERSION
) -> dict[tuple[str, str], int]:
    """This user's Category ids for taxonomy `version`, inserting them once.
    Older versions' rows stay: Corrections made on them still point there."""
    db = Scoped(conn, user_id)
    cat = schema.category
    rows = db.execute(
        db.select(cat, cat.c.id, cat.c.parent_id, cat.c.name).where(
            cat.c.taxonomy_version == version
        )
    ).all()
    if not rows:
        for top, subs in TAXONOMIES[version].items():
            parent = db.insert(cat, name=top, taxonomy_version=version)
            for sub in subs:
                db.insert(cat, name=sub, parent_id=parent, taxonomy_version=version)
        return seed_taxonomy(conn, user_id, version)
    names = {r.id: r.name for r in rows}
    return {(names[r.parent_id], r.name): r.id for r in rows if r.parent_id}


def _decided(db: Scoped) -> set[int]:
    """Merchant ids holding a decision keyed by `merchant_id`: an active
    Merchant-wide Correction, "Always ask", or any registry entry."""
    k, m, e = schema.correction, schema.merchant, schema.subscription_entry
    return {
        *db.execute(
            db.select(k, k.c.merchant_id).where(
                k.c.merchant_id.is_not(None), k.c.reverted_at.is_(None)
            )
        ).scalars(),
        *db.execute(db.select(m, m.c.id).where(m.c.always_ask.is_not(None))).scalars(),
        *db.execute(db.select(e, e.c.merchant_id)).scalars(),
    }


def _keep_merchants(
    db: Scoped,
    names: Mapping[int, str],
    merchant_ids: dict[str, int],
    ids: Mapping[Category, int],
) -> dict[int, str]:
    """Keep Merchant ids when a base change renames a descriptor family
    (#215: NETFLIX.COM is now Netflix), so decisions keyed by `merchant_id`
    stay in force. `names` is each Transaction's new Merchant name.

    A Merchant no Transaction is named after any more is renamed (same row)
    to the new name most of its Transactions take, if that name is free.
    A decided Merchant that cannot be renamed keeps its Transactions, and a
    new name only it gave up is an alias of it, so later imports join it too.
    Returns the Merchant name for each Transaction that keeps another one."""
    c, m = schema.categorisation, schema.merchant
    prior: dict[int, int] = {
        r.transaction_id: r.merchant_id
        for r in db.execute(
            db.select(c, c.c.transaction_id, c.c.merchant_id).where(
                c.c.merchant_id.is_not(None)
            )
        )
    }
    name_of = {i: n for n, i in merchant_ids.items()}
    decided = _decided(db)
    moves: dict[int, Counter[str]] = {}
    for tid, new in names.items():
        if (was := prior.get(tid)) is not None and new != name_of[was]:
            moves.setdefault(was, Counter())[new] += 1
    wanted = set(names.values())
    for p in sorted(moves, key=lambda p: (p not in decided, p)):  # decided first
        new = min(moves[p], key=lambda n: (-moves[p][n], n))
        if name_of[p] in wanted or new in merchant_ids:
            continue
        default = m.c.default_category_id
        if new in MERCHANTS:  # the seeded default; a Correction still beats it
            default = func.coalesce(default, ids[MERCHANTS[new][1]])
        db.execute(
            db.update(m)
            .where(m.c.id == p)
            .values(name=new, default_category_id=default)
        )
        merchant_ids[new] = merchant_ids.pop(name_of[p])
        name_of[p] = new
    alias: dict[str, str] = {}
    for p in sorted(decided & moves.keys()):
        for new in sorted(moves[p]):
            if new not in merchant_ids:
                alias.setdefault(new, name_of[p])
    keep: dict[int, str] = {}
    for tid, new in names.items():
        was = prior.get(tid)
        if was is not None and was in decided and new != name_of[was]:
            keep[tid] = name_of[was]
        elif new in alias:
            keep[tid] = alias[new]
    return keep


def run(conn: Connection, user_id: int) -> int:
    """(Re)categorise every Transaction of this user; returns how many.

    Derived data: recomputed in full so new Transfer links, rules or taxonomy
    versions apply to the whole history. shortcut: full recompute per import;
    make it incremental by ledger_version if it gets slow.
    """
    db = Scoped(conn, user_id)
    ledger = Ledger(conn, user_id)
    ids = seed_taxonomy(conn, user_id)
    merchant_ids = {m.name: m.id for m in ledger.merchants()}
    txns = ledger.transactions(merged=True)  # an undone merge needs its row ready
    names = {
        t.id: m
        for t in txns
        if (
            m := classify(
                t.description,
                t.amount,
                t.bank_type,
                transfer=t.transfer_with is not None,
            ).merchant
        )
    }
    keep = _keep_merchants(db, names, merchant_ids, ids)
    # seed only the base Merchants that appear: not all of them, so the
    # Merchant list stays the user's own
    for name in sorted({keep.get(i, m) for i, m in names.items()} & MERCHANTS.keys()):
        if name not in merchant_ids:
            merchant_ids[name] = db.insert(
                schema.merchant, name=name, default_category_id=ids[MERCHANTS[name][1]]
            )
    # the merchant table is the source of defaults; the Ledger gives them in
    # today's names, so one set on an older taxonomy version still applies
    defaults = {
        m.name: m.default_category
        for m in ledger.merchants()
        if m.default_category in ids
    }
    # "Always ask" Merchants: abstain, and (when learning lands, ADR 0015)
    # never learn from them
    ask = {m.name for m in ledger.merchants() if m.always_ask}
    db.execute(db.delete(schema.categorisation))
    for t in txns:
        kept = keep.get(t.id)
        c = classify(
            t.description,
            t.amount,
            t.bank_type,
            transfer=t.transfer_with is not None,
            defaults=defaults,
            ask=() if kept else ask,
        )
        if kept:  # the decided Merchant's row, with the base's Category
            c = (
                Classification(c.kind, kept, None, "always_ask")
                if kept in ask
                else replace(c, merchant=kept)
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
    auto.apply(conn, user_id, ids)  # #215 B: the model, last and gated
    subscriptions.recognise(ledger)  # #104: needs the Merchants just assigned
    return len(txns)
