"""Seeded synthetic personas for the playbook evaluation (#254,
docs/ML_EVALUATIONS.md). Synthetic only: every Merchant is fictional.

Each persona takes the bills, groceries and everyday spending of one
`synthetic_history` user (read-only), replaces its Subscriptions rows with a
controlled series, and gets a savings balance sized from its own essential
spend. `gold` names the playbook cards that should apply, set by how the
persona was built, never by running the Engine:

- **emergency-fund** applies when savings cover under three months of
  essentials; personas sit at 0, 0.2, 0.3, 1, 1.5 and 2.5 months (applies)
  or 3.5, 6 and 8 (does not), so none sits on the boundary.
- **subscription-creep** applies when Subscriptions spend rose in each of the
  last three complete months. Near misses: two rises only, and an annual
  renewal that jumps once and falls back.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

import synthetic_history as sh
from finance_agent.engine.detectors import Row

DATASET_VERSION = 1
MONTHS = 14  # of history before "today", from synthetic_history's start
TODAY = sh.on_day(MONTHS, 10)  # ten days into the month after the history
ESSENTIAL_TOPS = {"Bills"}
ESSENTIAL_LEAVES = {
    ("Food & Drink", "Groceries"),
    ("Health", "Pharmacy"),
    ("Transport", "Public transport"),
    ("Transport", "Fuel"),
}
SUBS = ("Subscriptions", "Streaming")


@dataclass(frozen=True)
class Persona:
    name: str
    rows: tuple[Row, ...]  # Spending and Refunds, signed pence
    savings_balance: int | None  # pence across savings Accounts
    today: date
    gold: frozenset[str]  # ids of the cards that should apply


def _essential(category: tuple[str, str]) -> bool:
    return category[0] in ESSENTIAL_TOPS or category in ESSENTIAL_LEAVES


def _base(hid: int) -> list[Row]:
    """One synthetic user's money out, less every Subscriptions row."""
    h = sh.generate(histories=hid + 1, months=MONTHS)[hid]
    return [
        Row(t.id, t.date, t.merchant, t.category, t.amount)
        for t in h.txns
        if t.amount < 0
        and t.kind not in ("salary", "transfer")
        and t.category[0] != "Subscriptions"
    ]


def _monthly_essentials(rows: list[Row]) -> Fraction:
    """Mean essential spend over the last three complete months."""
    last = sh.month_index(TODAY) - 1
    total = -sum(
        r.amount
        for r in rows
        if _essential(r.category) and last - 2 <= sh.month_index(r.posted_on) <= last
    )
    return Fraction(total, 3)


def _subs(
    rows: list[Row], starts: list[int], price: int = 999, annual_at: int | None = None
) -> list[Row]:
    """A monthly subscription from each month in `starts`; an annual fee in
    month `annual_at`. Ids follow on from `rows`."""
    out = list(rows)
    nid = max((r.id for r in rows), default=0) + 1
    for k, start in enumerate(starts):
        for m in range(start, MONTHS):
            out.append(
                Row(nid, sh.on_day(m, 3 + k), f"STREAMLY PLUS {k}", SUBS, -price)
            )
            nid += 1
    if annual_at is not None:
        out.append(Row(nid, sh.on_day(annual_at, 20), "CLOUDNOTE ANNUAL", SUBS, -6_999))
    return out


def _persona(
    name: str,
    hid: int,
    months_saved: Fraction | None,
    starts: list[int],
    gold: set[str],
    annual_at: int | None = None,
) -> Persona:
    base = _base(hid)
    rows = _subs(base, starts, annual_at=annual_at)
    savings = (
        None if months_saved is None else int(months_saved * _monthly_essentials(base))
    )
    return Persona(name, tuple(rows), savings, TODAY, frozenset(gold))


EF, SC = "emergency-fund", "subscription-creep"
L = MONTHS - 1  # the last complete month


def personas() -> list[Persona]:
    return [
        # one long-standing subscription: spend flat
        _persona("student", 0, Fraction(1), [0], {EF}),
        # a new subscription two months ago, another last month: two rises
        _persona("early career", 1, Fraction(5, 2), [0, L - 1, L], {EF}),
        # a new subscription in each of the last three months
        _persona("subscription creeper", 2, Fraction(6), [0, L - 2, L - 1, L], {SC}),
        _persona("credit-card revolver", 3, Fraction(0), [0], {EF}),
        _persona("payday-tight", 4, Fraction(1, 5), [0], {EF}),
        _persona("goal saver", 5, Fraction(8), [0], set()),
        Persona("no history", (), 50_000, TODAY, frozenset()),
        # low savings and creeping subscriptions from four months ago
        _persona("mixed", 1, Fraction(3, 2), [L - 3, L - 2, L - 1, L], {EF, SC}),
        # an annual renewal the month before last on a flat subscription
        _persona("annual blip", 2, Fraction(7, 2), [0], set(), annual_at=L - 1),
        # a little savings but no subscriptions at all
        _persona("no subscriptions", 4, Fraction(3, 10), [], {EF}),
    ]


def fingerprint(ps: list[Persona]) -> str:
    # gold sorted: a frozenset's repr order follows the per-run string hash
    stable = [(p.name, p.rows, p.savings_balance, p.today, sorted(p.gold)) for p in ps]
    return hashlib.sha256(repr(stable).encode()).hexdigest()
