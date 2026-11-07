"""Seeded synthetic multi-month histories with planted events (#146), for the
detector, ranking and forecast harnesses (docs/ML_EVALUATIONS.md). Synthetic
only: every Merchant is fictional. Amounts are signed integer pence.

Each history is one current Account of a fictional user:

- **Payday cycle.** Salary lands on a fixed day each month, then a savings
  sweep resets the balance to a buffer `B`, so each payday cycle starts level.
  Discretionary spending is three times as likely in the week after payday.
- **Seasonality.** Monthly spend per Category follows a seasonal profile
  (December shopping, summer holidays, winter energy).
- **Planted events**, with ground truth in `History.events`:
  - `price_rise`: one subscription's monthly charge goes up for good;
  - `category_spike`: a Category's monthly spend at 2-3x its seasonal normal,
    only once the ADR 0015 six-month baseline exists;
  - `forgotten_renewal`: an annual charge renewing 12 months on, with nothing
    from that Merchant in between;
  - `payday_shortfall`: an emergency expense early in one cycle that leaves the
    Account in credit, after which ordinary spending takes it below zero
    before the next payday. `B` is sized so no other cycle ever dips.
"""

import calendar
import hashlib
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

SEED = 146
DATASET_VERSION = 1
START = date(2023, 1, 1)
MONTHS = 36
MIN_MONTHS = 14  # room for a renewal 12 months on and a six-month baseline
BASELINE_MONTHS = 6  # ADR 0015: personal baselines need six months
SPIKE_DETECTABLE = 1.6  # a planted spike is at least this times the normal
MARGIN = 20_000  # every unplanted cycle ends at least £200 in credit

EVENT_TYPES = ("price_rise", "category_spike", "forgotten_renewal", "payday_shortfall")

type Category = tuple[str, str]
NONE: Category = ("Uncategorised", "Uncategorised")  # salary and transfers


def label(c: Category) -> str:
    return f"{c[0]} / {c[1]}"


@dataclass(frozen=True)
class Txn:
    id: int
    date: date
    merchant: str
    category: Category
    amount: int  # signed pence: negative is money out
    balance: int  # running balance after this row
    kind: str  # salary, transfer, recurring, discretionary or oneoff


@dataclass(frozen=True)
class Event:
    type: str  # one of EVENT_TYPES
    date: date  # when the event shows in the Transactions
    month: int  # month index from START
    key: str  # Merchant (price_rise, forgotten_renewal), Category label, or ""
    category: Category
    impact: int  # pence; annualised for a price rise
    evidence: tuple[int, ...]  # Txn ids


@dataclass(frozen=True)
class History:
    id: int
    months: int
    payday: int  # day of month
    opening: int
    txns: tuple[Txn, ...]
    events: tuple[Event, ...]
    base: dict[Category, int] = field(hash=False)  # monthly spend before season
    # planted relevance (percent) the user gives an Insight's type and Category
    care_type: dict[str, int] = field(hash=False)
    care_category: dict[str, int] = field(hash=False)


# Discretionary Categories: (base range, txns a month, Merchants, season % by month)
FLAT = (100,) * 12
DISCRETIONARY: dict[
    Category, tuple[tuple[int, int], tuple[int, int], tuple[str, ...], tuple[int, ...]]
] = {
    ("Food & Drink", "Groceries"): (
        (25_000, 40_000),
        (8, 14),
        ("MARLOW GROCERS", "FENWICK FARM SHOP"),
        (100, 95, 100, 100, 100, 100, 100, 100, 100, 100, 105, 125),
    ),
    ("Food & Drink", "Eating out"): (
        (6_000, 15_000),
        (3, 7),
        ("THE COPPER KETTLE", "BLUE DOOR BISTRO"),
        (85, 85, 95, 100, 105, 110, 120, 120, 100, 95, 100, 145),
    ),
    ("Food & Drink", "Takeaway"): (
        (3_000, 8_000),
        (2, 5),
        ("WOKWAY KITCHEN", "KESTREL KITCHEN"),
        (120, 120, 105, 100, 95, 90, 85, 85, 95, 100, 110, 115),
    ),
    ("Food & Drink", "Coffee"): (
        (2_000, 5_000),
        (6, 15),
        ("BEAN THERE COFFEE", "MOONBEAM ROASTERS"),
        FLAT,
    ),
    ("Transport", "Public transport"): (
        (5_000, 12_000),
        (6, 15),
        ("NORTHLINE RAIL", "ZIPLINE BUS CO"),
        (100, 100, 100, 100, 100, 100, 90, 70, 105, 105, 100, 80),
    ),
    ("Shopping", "Online"): (
        (5_000, 15_000),
        (2, 6),
        ("PARCELPOINT ONLINE", "BRIGHTCART"),
        (80, 80, 90, 95, 100, 100, 95, 95, 100, 105, 135, 200),
    ),
    ("Travel", "Holidays"): (
        (3_000, 8_000),
        (1, 2),
        ("SUNTRAIL HOLIDAYS", "PINEWOOD LODGE"),
        (50, 50, 50, 70, 90, 150, 300, 350, 120, 60, 50, 80),
    ),
}
ENERGY_SEASON = (160, 150, 130, 110, 90, 70, 60, 60, 75, 100, 130, 155)
SUBSCRIPTIONS: tuple[tuple[str, Category, int], ...] = (
    ("STREAMLY", ("Subscriptions", "Streaming"), 1_099),
    ("PIXELFORGE SOFTWARE", ("Subscriptions", "Software"), 799),
    ("ORCHARD BOOK CLUB", ("Subscriptions", "Memberships"), 1_500),
    ("IRONWORKS GYM", ("Health", "Fitness"), 3_500),
    ("SKYVAULT STORAGE", ("Subscriptions", "Cloud storage"), 299),
)
ANNUAL = ("CLOUDNOTE ANNUAL", ("Subscriptions", "Software"))
ONE_OFFS: tuple[tuple[str, Category], ...] = (
    ("VOLTSPARK ELECTRONICS", ("Shopping", "Electronics")),
    ("FIXRIGHT PLUMBING", ("Housing", "Maintenance")),
    ("ROYAL STAGE TICKETS", ("Entertainment", "Events")),
    ("SKYWING AIR", ("Travel", "Flights")),
    ("GREENLEAF DENTAL", ("Health", "Medical")),
)
EMERGENCY = ("HOMEFIX EMERGENCY REPAIR", ("Housing", "Maintenance"))
RANK = {"salary": 0, "transfer": 1}  # within a day: salary, sweep, then spending


def month_start(m: int) -> date:
    y, mo = divmod(START.month - 1 + m, 12)
    return date(START.year + y, mo + 1, 1)


def on_day(m: int, day: int) -> date:
    first = month_start(m)
    return first.replace(day=min(day, calendar.monthrange(first.year, first.month)[1]))


def month_index(d: date) -> int:
    return (d.year - START.year) * 12 + d.month - START.month


def payday_on_or_before(h: History, d: date) -> date:
    p = on_day(month_index(d), h.payday)
    return p if p <= d else on_day(month_index(d) - 1, h.payday)


def expected_spend(h: History, category: Category, m: int) -> int:
    """The seasonal normal: what the user spends in a month with no spike."""
    season = DISCRETIONARY[category][3]
    return h.base[category] * season[month_start(m).month - 1] // 100


def month_spend(h: History, category: Category, m: int) -> int:
    """Money out in `category` during month `m`, in pence (positive)."""
    return -sum(
        t.amount
        for t in h.txns
        if t.category == category and t.amount < 0 and month_index(t.date) == m
    )


def fingerprint(histories: list[History]) -> str:
    return hashlib.sha256(repr(histories).encode()).hexdigest()


def generate(
    seed: int = SEED, histories: int = 6, months: int = MONTHS
) -> list[History]:
    """`histories` fictional users over `months` months; deterministic per seed."""
    if months < MIN_MONTHS:
        raise ValueError(f"need at least {MIN_MONTHS} months")
    return [_history(seed, i, months) for i in range(histories)]


# A row before ids and balances: (date, kind, merchant, category, amount, tag).
# The tag marks rows that evidence a planted event.
type Draft = tuple[date, str, str, Category, int, str]


def _history(seed: int, hid: int, months: int) -> History:
    rng = random.Random(seed * 1_000 + hid)  # noqa: S311 - synthetic data, not security
    payday = rng.choice((1, 15, 25, 28))
    rows: list[Draft] = []
    events: list[
        tuple[str, str, Category, int, str]
    ] = []  # type, key, cat, impact, tag

    # salary
    salary = rng.randint(220_000, 380_000)
    rows += [
        (on_day(m, payday), "salary", "ACME LTD SALARY", NONE, salary, "")
        for m in range(months)
    ]

    # bills
    rent = rng.randint(80_000, 140_000)
    tax = rng.randint(12_000, 18_000)
    energy = rng.randint(6_000, 12_000)
    phone = rng.randint(1_500, 3_000)
    for m in range(months):
        rows.append(
            (
                on_day(m, 1),
                "recurring",
                "MAPLE LETTINGS RENT",
                ("Bills", "Rent or mortgage"),
                -rent,
                "",
            )
        )
        rows.append(
            (
                on_day(m, 5),
                "recurring",
                "HALDEN COUNCIL TAX",
                ("Bills", "Council tax"),
                -tax,
                "",
            )
        )
        bill = (
            energy
            * ENERGY_SEASON[month_start(m).month - 1]
            * rng.randint(95, 105)
            // 10_000
        )
        rows.append(
            (
                on_day(m, 12),
                "recurring",
                "BRIGHTWATER ENERGY",
                ("Bills", "Energy"),
                -bill,
                "",
            )
        )
        rows.append(
            (
                on_day(m, 18),
                "recurring",
                "KESTREL MOBILE",
                ("Bills", "Phone & broadband"),
                -phone,
                "",
            )
        )

    # subscriptions, one of which rises in price
    subs = rng.sample(SUBSCRIPTIONS, rng.randint(3, 4))
    risen, rise_month = rng.choice(subs)[0], rng.randint(BASELINE_MONTHS, months - 1)
    for merchant, cat, price in subs:
        day = rng.randint(1, 28)
        new = price * rng.randint(105, 130) // 100
        for m in range(months):
            up = merchant == risen and m >= rise_month
            tag = (
                f"rise{m - rise_month}"
                if merchant == risen and m - rise_month in (-1, 0)
                else ""
            )
            rows.append(
                (
                    on_day(m, day),
                    "recurring",
                    merchant,
                    cat,
                    -(new if up else price),
                    tag,
                )
            )
            if up and m == rise_month:
                events.append(
                    ("price_rise", merchant, cat, (new - price) * 12, "rise0")
                )

    # an annual renewal
    k, day, fee = (
        rng.randint(0, min(11, months - 13)),
        rng.randint(1, 28),
        rng.randint(4_999, 8_999),
    )
    for n, m in enumerate(range(k, months, 12)):
        rows.append(
            (on_day(m, day), "recurring", ANNUAL[0], ANNUAL[1], -fee, f"annual{n}")
        )
        if n:
            events.append(
                ("forgotten_renewal", ANNUAL[0], ANNUAL[1], fee, f"annual{n}")
            )

    # one-off purchases: a Merchant seen once
    for merchant, cat in rng.sample(ONE_OFFS, rng.randint(2, 4)):
        d = month_start(0) + timedelta(
            days=rng.randrange((on_day(months - 1, 28) - START).days)
        )
        rows.append((d, "oneoff", merchant, cat, -rng.randint(3_000, 40_000), ""))

    # discretionary spending, seasonal, payday-weighted, with planted spikes
    base = {c: rng.randint(*spec[0]) for c, spec in DISCRETIONARY.items()}
    spikes = set(
        zip(
            rng.sample(sorted(DISCRETIONARY), 2),
            (rng.randint(BASELINE_MONTHS, months - 1) for _ in range(2)),
            strict=True,
        )
    )
    for cat, ((_, _), (lo, hi), merchants, season) in DISCRETIONARY.items():
        for m in range(months):
            normal = base[cat] * season[month_start(m).month - 1] // 100
            target = normal * rng.randint(85, 115) // 100
            tag = ""
            if (cat, m) in spikes:
                spiked = target * rng.randint(200, 300) // 100
                events.append(
                    ("category_spike", label(cat), cat, spiked - target, f"spike{m}")
                )
                target, tag = spiked, f"spike{m}"
            first = month_start(m)
            days = [
                first + timedelta(days=i)
                for i in range(calendar.monthrange(first.year, first.month)[1])
            ]
            weights = [
                3
                if 0 <= (d - on_day(month_index(d), payday)).days < 7
                or 0 <= (d - on_day(month_index(d) - 1, payday)).days < 7
                else 1
                for d in days
            ]
            n = rng.randint(lo, hi)
            shares = [rng.randint(1, 10) for _ in range(n)]
            amounts = [target * s // sum(shares) for s in shares]
            amounts[0] += target - sum(amounts)
            for a, d in zip(amounts, rng.choices(days, weights, k=n), strict=True):
                rows.append((d, "discretionary", rng.choice(merchants), cat, -a, tag))

    care_type = {t: rng.choice((10, 30, 60, 90)) for t in EVENT_TYPES}
    care_category = {
        label(c): rng.choice((20, 50, 80, 100))
        for c in sorted({r[3] for r in rows} - {NONE})
    }

    return _assemble(
        rng, hid, months, payday, rows, events, base, care_type, care_category
    )


def _assemble(
    rng: random.Random,
    hid: int,
    months: int,
    payday: int,
    rows: list[Draft],
    planted: list[tuple[str, str, Category, int, str]],
    base: dict[Category, int],
    care_type: dict[str, int],
    care_category: dict[str, int],
) -> History:
    rows.sort(key=lambda r: (r[0], RANK.get(r[1], 2), r[2], r[4]))
    # payday cycles: each starts at a salary row; the sweep levels it at B
    starts = [i for i, r in enumerate(rows) if r[1] == "salary"]
    bounds = list(zip([0, *starts], [*starts, len(rows)], strict=True))
    spent = [-sum(r[4] for r in rows[a:b] if r[4] < 0) for a, b in bounds]
    buffer = max(spent) + MARGIN

    # the shortfall: an emergency expense early in a cycle with a next payday
    a, b = bounds[rng.randint(2, len(bounds) - 2)]
    when = rows[a][0] + timedelta(days=rng.randint(2, 6))
    at = next((i for i in range(a + 1, b) if rows[i][0] >= when), a + 1)
    before = buffer + sum(r[4] for r in rows[a + 1 : at] if r[1] != "salary")
    after = -sum(r[4] for r in rows[at:b])  # spending still to come this cycle
    expense = before - after // 2  # leaves after // 2 in credit, then dips
    rows.insert(
        at, (rows[at][0], "oneoff", EMERGENCY[0], EMERGENCY[1], -expense, "shortfall")
    )

    txns: list[Txn] = []
    tagged: dict[str, int] = {}
    balance = buffer
    for r in rows:
        d, kind, merchant, cat, amount, tag = r
        balance += amount
        txns.append(Txn(len(txns) + 1, d, merchant, cat, amount, balance, kind))
        if kind == "salary":
            sweep = buffer - balance
            balance += sweep
            txns.append(
                Txn(len(txns) + 1, d, "SAVINGS SWEEP", NONE, sweep, balance, "transfer")
            )
        if tag:
            tagged.setdefault(tag, txns[-1].id)

    by_id = {t.id: t for t in txns}
    events: list[Event] = []
    for etype, key, cat, impact, tag in planted:
        if etype == "price_rise":
            ev = (tagged["rise-1"], tagged["rise0"])
        elif etype == "forgotten_renewal":
            n = int(tag.removeprefix("annual"))
            ev = (tagged[f"annual{n - 1}"], tagged[tag])
        else:
            ev = tuple(
                t.id
                for t in txns
                if t.category == cat
                and month_index(t.date) == int(tag[5:])
                and t.kind == "discretionary"
            )
        d = (
            by_id[ev[-1]].date
            if etype != "category_spike"
            else month_start(int(tag[5:]))
        )
        events.append(Event(etype, d, month_index(d), key, cat, impact, ev))
    ex = by_id[tagged["shortfall"]]
    cycle = [t for t in txns if t.id >= ex.id and t.date < ex.date + timedelta(days=31)]
    dipped = [t for t in cycle if t.balance < 0]
    events.append(
        Event(
            "payday_shortfall",
            dipped[0].date,
            month_index(dipped[0].date),
            "",
            EMERGENCY[1],
            -min(t.balance for t in dipped),
            (ex.id,),
        )
    )
    events.sort(key=lambda e: (e.date, e.type, e.key))
    return History(
        hid,
        months,
        payday,
        buffer,
        tuple(txns),
        tuple(events),
        base,
        care_type,
        care_category,
    )
