"""Detectors (#148, docs/AI_DESIGN.md, ADR 0015): typed Insight candidates.

Pure functions over a Ledger's Spending rows (`Row`: signed GBP pence, money
out negative, Refunds positive); the caller leaves out income, transfers and
foreign rows still needing an FX rate (never guessed), and passes as `stopped`
the Merchants the user cancelled or marked not a subscription. Each
returns `Insight`s with an impact in integer pence computed here, a confidence
in percent and the ids of the Transactions that evidence it, each dated the
day the rows first support it, so a run over a whole history replays what
the Engine would have raised day by day. An upcoming renewal is dated
`LEAD_DAYS` before it is due, which can lie after the last row. Results
depend only on the multiset of rows, never on their order.

- `recurring_payments`: each Merchant's current series (`categorise.recurring`).
- `price_increases`: a charge above the series before it, at its cadence. A
  price step starts a new run (#177), so the step is judged between the runs.
- `category_spikes`: a month's net spend over 1.5x its seasonal baseline (#147).
- `upcoming_renewals`: an annual series, or below the recurring gate a
  Subscriptions charge with nothing from the Merchant for 300 days around it.

The learned gates of ADR 0015 return `Insufficient` when no key clears them.
Confidences are fixed priors per rule until the ranker (#150) calibrates them.
"""

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal, NamedTuple

from finance_agent.categorise.recurring import (
    CADENCES,
    MIN_CHARGES,
    TOLERANCE_PCT,
    Recurrence,
    detect_recurring,
)
from finance_agent.engine.baselines import (
    MIN_MONTHS,
    Insufficient,
    seasonal_baseline,
)

type Category = tuple[str, str]
type InsightType = Literal[
    "recurring", "price_increase", "category_spike", "upcoming_renewal", "shortfall"
]

PER_YEAR = {"weekly": 52, "monthly": 12, "annual": 1}
SPIKE = (3, 2)  # a month's net spend over 3/2 of its seasonal baseline
LEAD_DAYS = 28  # a renewal is raised four weeks before it is due
ANNUAL_GAP = 300  # days with nothing from a Merchant around an annual charge
CONF_RECURRING = 90
CONF_PRICE = 70  # one dearer charge: could yet be a one-off extra
CONF_SPIKE = 80  # against the same month in earlier years
CONF_SPIKE_EARLY = 60  # first year: against the last six months
CONF_RENEWAL_SERIES = 90
CONF_RENEWAL_TWICE = 70  # two Subscriptions charges a year apart
CONF_RENEWAL_ONCE = 50


class Row(NamedTuple):
    id: int  # Transaction id
    posted_on: date
    merchant: str
    category: Category
    amount: int  # signed pence: money out negative, a Refund positive


@dataclass(frozen=True)
class Insight:
    type: InsightType
    key: str  # the Merchant, or "Category / Subcategory" for a spike
    on: date  # when the rows first support it
    impact: int  # pence: annual cost, annual rise, excess so far, or the fee
    confidence: int  # percent
    evidence: tuple[int, ...]  # Transaction ids, ascending


def _charges(rows: Iterable[Row]) -> dict[str, list[Row]]:
    """Money out per Merchant, in the order `detect_recurring` sorts it."""
    out: defaultdict[str, list[Row]] = defaultdict(list)
    for r in sorted(rows, key=lambda r: (r.posted_on, -r.amount, r)):
        if r.amount < 0:
            out[r.merchant].append(r)
    return out


def _series(charges: Sequence[Row]) -> Recurrence | None:
    return detect_recurring((c.posted_on, -c.amount) for c in charges)


def _gate(by_merchant: dict[str, list[Row]]) -> Insufficient | None:
    have = max((len(c) for c in by_merchant.values()), default=0)
    return None if have >= MIN_CHARGES else Insufficient(have, MIN_CHARGES)


def recurring_payments(rows: Iterable[Row]) -> list[Insight] | Insufficient:
    """Each Merchant's current series, as of its latest charge."""
    by_merchant = _charges(rows)
    if (short := _gate(by_merchant)) is not None:
        return short
    out: list[Insight] = []
    for merchant, charges in sorted(by_merchant.items()):
        rec = _series(charges)
        if rec is None:
            continue
        run = charges[-rec.charge_count :]
        out.append(
            Insight(
                "recurring",
                merchant,
                rec.last_on,
                rec.typical_minor * PER_YEAR[rec.interval],
                CONF_RECURRING,
                tuple(sorted(c.id for c in run)),
            )
        )
    return out


def price_increases(
    rows: Iterable[Row], stopped: Collection[str] = ()
) -> list[Insight] | Insufficient:
    """A charge over 5% above the series it follows, one cadence after the
    series' last charge; impact is the rise over a year. `stopped` Merchants
    (cancelled, or marked not a subscription) are never flagged."""
    by_merchant = _charges(rows)
    if (short := _gate(by_merchant)) is not None:
        return short
    out: list[Insight] = []
    for merchant, charges in sorted(by_merchant.items()):
        if merchant in stopped:
            continue
        for i in range(MIN_CHARGES, len(charges)):
            new, prev = -charges[i].amount, charges[i - 1]
            if new * 100 <= -prev.amount * (100 + TOLERANCE_PCT):
                continue  # cheap filter before re-detecting the series
            rec = _series(charges[:i])
            if rec is None or rec.last_on != prev.posted_on:
                continue
            lo, hi = CADENCES[rec.interval]
            gap = (charges[i].posted_on - prev.posted_on).days
            if lo <= gap <= hi and new * 100 > rec.typical_minor * (
                100 + TOLERANCE_PCT
            ):
                out.append(
                    Insight(
                        "price_increase",
                        merchant,
                        charges[i].posted_on,
                        (new - rec.typical_minor) * PER_YEAR[rec.interval],
                        CONF_PRICE,
                        tuple(sorted((prev.id, charges[i].id))),
                    )
                )
    return out


def _month(d: date) -> int:
    return d.year * 12 + d.month - 1


def category_spikes(rows: Iterable[Row]) -> list[Insight] | Insufficient:
    """A Category-month whose spend, net of Refunds, passes SPIKE x its
    seasonal baseline (#147), flagged on the day it crosses with the excess
    so far; evidence is the month's rows to that day."""
    by_cat: defaultdict[Category, list[Row]] = defaultdict(list)
    for r in rows:
        by_cat[r.category].append(r)
    out: list[Insight] = []
    have, gated = 0, False
    for cat, rs in sorted(by_cat.items()):
        pairs = [(r.posted_on, r.amount) for r in rs]
        by_month: defaultdict[int, list[Row]] = defaultdict(list)
        for r in rs:
            by_month[_month(r.posted_on)].append(r)
        for m, month in sorted(by_month.items()):
            b = seasonal_baseline(pairs, date(m // 12, m % 12 + 1, 1))
            if isinstance(b, Insufficient):
                have = max(have, b.have)
                continue
            gated = True
            if b.expected == 0:
                continue
            spent, seen = 0, list[int]()
            for r in sorted(month, key=lambda r: (r.posted_on, r)):
                spent -= r.amount  # net of Refunds, like the baseline
                seen.append(r.id)
                if SPIKE[1] * spent > SPIKE[0] * b.expected:
                    out.append(
                        Insight(
                            "category_spike",
                            f"{cat[0]} / {cat[1]}",
                            r.posted_on,
                            spent - b.expected,
                            CONF_SPIKE if b.seasonal else CONF_SPIKE_EARLY,
                            tuple(sorted(seen)),
                        )
                    )
                    break
    return out if gated else Insufficient(have, MIN_MONTHS)


def _year_on(d: date) -> date:
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 February
        return date(d.year + 1, 2, 28)


def upcoming_renewals(
    rows: Iterable[Row], stopped: Collection[str] = ()
) -> list[Insight]:
    """A charge renewing a year on, raised LEAD_DAYS before it is due unless the
    Merchant charges again first: an annual series (any Category), or, below
    the recurring gate (a rule, not a learned component), a Subscriptions
    charge with nothing from the Merchant in the ANNUAL_GAP days before it.
    Impact is the fee last paid. `stopped` Merchants (cancelled, or marked
    not a subscription) never renew."""
    out: list[Insight] = []
    for merchant, charges in sorted(_charges(rows).items()):
        if merchant in stopped:
            continue
        for j, c in enumerate(charges):
            prev = charges[j - 1] if j else None
            if prev and (c.posted_on - prev.posted_on).days < ANNUAL_GAP:
                continue
            flag = _year_on(c.posted_on) - timedelta(days=LEAD_DAYS)
            if j + 1 < len(charges) and charges[j + 1].posted_on <= flag:
                continue
            rec = _series(charges[: j + 1])
            if rec is not None and rec.interval == "annual":
                confidence = CONF_RENEWAL_SERIES
            elif c.category[0] == "Subscriptions":
                confidence = CONF_RENEWAL_TWICE if prev else CONF_RENEWAL_ONCE
            else:
                continue
            out.append(
                Insight(
                    "upcoming_renewal", merchant, flag, -c.amount, confidence, (c.id,)
                )
            )
    return out


def detect(rows: Iterable[Row], stopped: Collection[str] = ()) -> list[Insight]:
    """Every detector's Insights; one below its gate contributes none.
    `stopped` Merchants get no price increase or renewal; their rows still
    count towards spikes."""
    rows = list(rows)
    out: list[Insight] = []
    for found in (
        recurring_payments(rows),
        price_increases(rows, stopped),
        category_spikes(rows),
        upcoming_renewals(rows, stopped),
    ):
        if not isinstance(found, Insufficient):
            out += found
    return sorted(out, key=lambda i: (i.on, i.type, i.key, i.evidence))
