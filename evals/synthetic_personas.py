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
  renewal that jumps once and falls back; a refunded charge ends a run.
- **small-daily-spends** applies when coffee, takeaways and eating out last
  month reach 170 pounds; the base users sit at 134 to 161 (no) or 178 to
  197 (yes).
- **credit-cards** applies when card interest was charged last month: only
  the revolver has any.
- **payday-planning** applies under half a month of savings; **student-money**
  from half a month to three (a first buffer); **saving-for-goal** from three
  months up; **payday-checkin** whenever essentials can be worked out.
- Short histories (one month, three months) and spending in other currencies
  (already converted to pence) check that thin or odd data is handled.
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction

import synthetic_history as sh
from finance_agent.engine.detectors import Row

DATASET_VERSION = 3
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
INTEREST = ("Fees & Interest", "Card interest")
GROCERIES = ("Food & Drink", "Groceries")
TRAVEL = ("Travel", "Holidays")


@dataclass(frozen=True)
class Persona:
    name: str
    rows: tuple[Row, ...]  # Spending and Refunds, signed pence
    savings_balance: int | None  # pence across savings Accounts
    today: date
    gold: frozenset[str]  # ids of the cards that should apply
    # what the user told the Assistant (#264), set by hand per persona
    memories: tuple["Memo", ...] = ()
    tone: str = "gentle"  # the tone the persona wants; gentle when never said
    want: tuple[tuple[str, int], ...] = ()  # (card id, gain 1..5) they care about
    ruled_out: frozenset[str] = frozenset()  # cards a constraint must remove


@dataclass(frozen=True)
class Memo:
    """A Memory as the playbook reads it."""

    type: str
    tags: tuple[str, ...]
    priority: int | None = None
    amount_minor: int | None = None


def _tone(t: str) -> Memo:
    return Memo("preference", ("tone", t))


def _goal(tag: str, priority: int, amount: int | None = None) -> Memo:
    return Memo("goal", (tag,), priority, amount)


# persona name -> (memories, gold gains by card id, cards a constraint removes)
TASTE: dict[str, tuple[tuple[Memo, ...], dict[str, int], set[str]]] = {
    "student": (
        (_tone("blunt"), _goal("student-money", 1), _goal("emergency-fund", 3)),
        {"student-money": 5, "emergency-fund": 3},
        set(),
    ),
    "credit-card revolver": (
        (_goal("credit-cards", 1), _goal("emergency-fund", 2)),
        {"credit-cards": 5, "emergency-fund": 4},
        set(),
    ),
    "subscription creeper": (
        (
            _tone("blunt"),
            _goal("saving-for-goal", 1, 500_000),
            _goal("subscription-creep", 2),
        ),
        {"saving-for-goal": 5, "subscription-creep": 4},
        set(),
    ),
    "payday-tight": (
        (_tone("blunt"), _goal("payday-planning", 1)),
        {"payday-planning": 5},
        set(),
    ),
    "goal saver": (
        (
            _goal("saving-for-goal", 1, 200_000),
            Memo("constraint", ("small-daily-spends",)),
        ),
        {"saving-for-goal": 5},
        {"small-daily-spends"},
    ),
    "mixed": (
        (_tone("gentle"), _goal("subscription-creep", 1), _goal("emergency-fund", 2)),
        {"subscription-creep": 5, "emergency-fund": 4},
        set(),
    ),
}


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
    extra: tuple[Row, ...] = (),
    since: int = 0,
) -> Persona:
    """`extra` rows are added; rows before month `since` are dropped (a short
    history). Savings are sized from the full base history."""
    base = _base(hid)
    rows = _subs(base, starts, annual_at=annual_at)
    nid = max(r.id for r in rows) + 1
    rows += [r._replace(id=nid + i) for i, r in enumerate(extra)]
    rows = [r for r in rows if sh.month_index(r.posted_on) >= since]
    savings = (
        None if months_saved is None else int(months_saved * _monthly_essentials(base))
    )
    return Persona(name, tuple(rows), savings, TODAY, frozenset(gold))


def _interest() -> tuple[Row, ...]:
    """Card interest on the 25th of every month."""
    return tuple(
        Row(0, sh.on_day(m, 25), "NORTHBANK CARD INTEREST", INTEREST, -3_872)
        for m in range(MONTHS)
    )


def _abroad() -> tuple[Row, ...]:
    """Spending in euros and dollars, already converted to pence at the day's
    rate, so the amounts are not round. One refund is converted too."""
    out = [
        Row(0, sh.on_day(m, 12), "MARCHE DU PORT", GROCERIES, -4_131 - 7 * m)
        for m in range(MONTHS - 4, MONTHS)
    ]
    out.append(Row(0, sh.on_day(MONTHS - 2, 14), "MARCHE DU PORT", GROCERIES, 1_273))
    out.append(Row(0, sh.on_day(MONTHS - 6, 8), "LAKESIDE HOTEL", TRAVEL, -23_849))
    return tuple(out)


EF, SC = "emergency-fund", "subscription-creep"
SD, SG, CC = "small-daily-spends", "saving-for-goal", "credit-cards"
PP, SM, PC = "payday-planning", "student-money", "payday-checkin"
L = MONTHS - 1  # the last complete month


def personas() -> list[Persona]:
    return [_taste(p) for p in _personas()]


def _taste(p: Persona) -> Persona:
    mems, want, out = TASTE.get(p.name, ((), {}, set()))
    tone = "blunt" if _tone("blunt") in mems else "gentle"
    return replace(
        p,
        memories=mems,
        tone=tone,
        want=tuple(sorted(want.items())),
        ruled_out=frozenset(out),
    )


def _personas() -> list[Persona]:
    return [
        # one long-standing subscription: spend flat
        _persona("student", 0, Fraction(1), [0], {EF, SD, SM, PC}),
        # a new subscription two months ago, another last month: two rises
        _persona("early career", 1, Fraction(5, 2), [0, L - 1, L], {EF, SM, PC}),
        # a new subscription in each of the last three months
        _persona(
            "subscription creeper",
            2,
            Fraction(6),
            [0, L - 2, L - 1, L],
            {SC, SD, SG, PC},
        ),
        # no savings, and card interest every month
        _persona(
            "credit-card revolver",
            3,
            Fraction(0),
            [0],
            {EF, CC, PP, PC},
            extra=_interest(),
        ),
        _persona("payday-tight", 4, Fraction(1, 5), [0], {EF, PP, PC}),
        _persona("goal saver", 5, Fraction(8), [0], {SD, SG, PC}),
        Persona("no history", (), 50_000, TODAY, frozenset()),
        # low savings and creeping subscriptions from four months ago
        _persona(
            "mixed", 1, Fraction(3, 2), [L - 3, L - 2, L - 1, L], {EF, SC, SM, PC}
        ),
        # an annual renewal the month before last on a flat subscription
        _persona("annual blip", 2, Fraction(7, 2), [0], {SD, SG, PC}, annual_at=L - 1),
        # a little savings but no subscriptions at all
        _persona("no subscriptions", 4, Fraction(3, 10), [], {EF, PP, PC}),
        # as the creeper, but last month's new subscription was refunded in
        # full, so the run of rises ends there
        _persona(
            "refunded subscription",
            2,
            Fraction(6),
            [0, L - 2, L - 1, L],
            {SD, SG, PC},
            extra=(Row(0, sh.on_day(L, 18), "STREAMLY PLUS 3", SUBS, 999),),
        ),
        # only last month and this one: no complete month to compare
        _persona("one month of history", 1, Fraction(3, 2), [0], set(), since=L),
        # three months: essentials can be worked out, subscriptions are flat
        _persona(
            "three months of history", 4, Fraction(3, 2), [0], {EF, SM, PC}, since=L - 3
        ),
        # euro and dollar spending in pence, with a refund
        _persona(
            "other currencies",
            0,
            Fraction(3, 2),
            [0],
            {EF, SD, SM, PC},
            extra=_abroad(),
        ),
    ]


def fingerprint(ps: list[Persona]) -> str:
    # gold sorted: a frozenset's repr order follows the per-run string hash
    stable = [
        (
            p.name,
            p.rows,
            p.savings_balance,
            p.today,
            sorted(p.gold),
            p.memories,
            p.tone,
            p.want,
            sorted(p.ruled_out),
        )
        for p in ps
    ]
    return hashlib.sha256(repr(stable).encode()).hexdigest()
