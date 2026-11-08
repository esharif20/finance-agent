"""Seeded synthetic income and essential-spending series with planted life
changes (#256), for `evals/changepoint.py`. Synthetic only: every Merchant is
fictional. Amounts are signed integer pence, Transaction ids unique per
history.

Each history is one fictional user over `MONTHS` months with four series
(`engine.changepoint.SERIES`):

- **income**: a monthly salary with small payroll noise;
- **rent**: a flat monthly rent;
- **bills**: council tax, energy with the winter season of
  `synthetic_history.ENERGY_SEASON`, and a phone bill;
- **groceries**: several shops a month around the seasonal groceries profile
  of `synthetic_history.DISCRETIONARY` (December up), +-15% noise.

Planted shifts (ground truth in `History.shifts`), each at month
`FIRST_SHIFT` or later and at least `GAP` months from any other event on its
series:

- `pay_rise`: income up 10-30% for good;
- `job_loss`: income down to 20-60% (a lower-paid job or benefits);
- `rent_rise`: rent up 10-25%;
- `childcare`: a new monthly childcare bill in bills, about £300-£900;
- `spike` (not a shift): one month of a series at 2-3x its level (a bonus,
  a boiler repair, a party) that a detector must not report.
"""

import hashlib
import random
from dataclasses import dataclass

import synthetic_history as sh
from finance_agent.engine.changepoint import SERIES, Row, Series

SEED = 256
DATASET_VERSION = 1
HISTORIES = 24
MONTHS = 36
FIRST_SHIFT = 6  # some shifts land before the nine-month gate opens
GAP = 9  # months between events on one series, so each is attributable

SHIFT_TYPES = ("pay_rise", "job_loss", "rent_rise", "childcare")
SERIES_OF: dict[str, Series] = {
    "pay_rise": "income",
    "job_loss": "income",
    "rent_rise": "rent",
    "childcare": "bills",
}
GROCERIES = sh.DISCRETIONARY[("Food & Drink", "Groceries")][3]


@dataclass(frozen=True)
class Shift:
    type: str  # one of SHIFT_TYPES, or "spike"
    series: Series
    month: int  # month index from synthetic_history.START


@dataclass(frozen=True)
class History:
    id: int
    months: int
    rows: dict[Series, tuple[Row, ...]]
    shifts: tuple[Shift, ...]


def fingerprint(histories: list[History]) -> str:
    return hashlib.sha256(repr(histories).encode()).hexdigest()


def generate(
    seed: int = SEED, histories: int = HISTORIES, months: int = MONTHS
) -> list[History]:
    """`histories` fictional users over `months` months; deterministic per seed."""
    return [_history(seed, i, months) for i in range(histories)]


def _free(rng: random.Random, taken: list[int], months: int) -> int | None:
    options = [
        m
        for m in range(FIRST_SHIFT, months - 4)
        if all(abs(m - t) >= GAP for t in taken)
    ]
    return rng.choice(options) if options else None


def _history(seed: int, hid: int, months: int) -> History:
    rng = random.Random(seed * 1_000 + hid)  # noqa: S311 - synthetic data, not security
    events: list[Shift] = []
    taken: dict[Series, list[int]] = {s: [] for s in SERIES}

    def plant(kind: str, series: Series) -> int | None:
        m = _free(rng, taken[series], months)
        if m is not None:
            taken[series].append(m)
            events.append(Shift(kind, series, m))
        return m

    # each history: maybe an income change, maybe rent, maybe childcare
    income_change = rng.choice(("pay_rise", "job_loss", None))
    if income_change:
        plant(income_change, "income")
    if rng.random() < 0.5:
        plant("rent_rise", "rent")
    if rng.random() < 0.4:
        plant("childcare", "bills")
    for series in rng.sample(SERIES, rng.randint(1, 3)):
        plant("spike", series)

    def at(series: Series, kind: str) -> int | None:
        return next(
            (e.month for e in events if e.series == series and e.type == kind), None
        )

    def spike(series: Series, m: int) -> int:
        return rng.randint(200, 300) if at(series, "spike") == m else 100

    salary = rng.randint(180_000, 400_000)
    factor = rng.randint(110, 130) if income_change == "pay_rise" else 100
    if income_change == "job_loss":
        factor = rng.randint(20, 60)
    rent = rng.randint(70_000, 150_000)
    rent_up = rng.randint(110, 125)
    tax, energy, phone = (
        rng.randint(10_000, 20_000),
        rng.randint(6_000, 14_000),
        rng.randint(1_500, 4_000),
    )
    childcare = rng.randint(30_000, 90_000)
    groceries = rng.randint(20_000, 45_000)

    rows: dict[Series, list[Row]] = {s: [] for s in SERIES}
    next_id = iter(range(1, 1_000_000))

    def add(series: Series, m: int, day: int, pence: int) -> None:
        rows[series].append((next(next_id), sh.on_day(m, day), pence))

    payday = rng.choice((1, 15, 25, 28))
    for m in range(months):
        calendar_month = sh.month_start(m).month - 1
        changed = income_change and m >= (at("income", income_change) or months)
        pay = salary * (factor if changed else 100) // 100
        pay = pay * rng.randint(99, 101) // 100 * spike("income", m) // 100
        add("income", m, payday, pay)

        r = rent * (rent_up if m >= (at("rent", "rent_rise") or months) else 100)
        add("rent", m, 1, -(r // 100) * spike("rent", m) // 100)

        bill_spike = spike("bills", m)
        add("bills", m, 5, -tax)
        add(
            "bills",
            m,
            12,
            -energy * sh.ENERGY_SEASON[calendar_month] * rng.randint(95, 105) // 10_000,
        )
        add("bills", m, 18, -phone)
        if m >= (at("bills", "childcare") or months):
            add("bills", m, 3, -childcare)
        if bill_spike != 100:  # a one-off bill the size of a normal month
            add("bills", m, 20, -(tax + energy + phone) * (bill_spike - 100) // 100)

        target = (
            groceries
            * GROCERIES[calendar_month]
            * rng.randint(85, 115)
            // 10_000
            * spike("groceries", m)
            // 100
        )
        shops = rng.randint(6, 12)
        shares = [rng.randint(1, 10) for _ in range(shops)]
        amounts = [target * s // sum(shares) for s in shares]
        amounts[0] += target - sum(amounts)
        for a in amounts:
            add("groceries", m, rng.randint(1, 28), -a)

    events.sort(key=lambda e: (e.month, e.series, e.type))
    return History(hid, months, {s: tuple(rows[s]) for s in SERIES}, tuple(events))
