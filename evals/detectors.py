"""Detector evaluation (#146, docs/ML_EVALUATIONS.md): precision and recall per
planted event type on the synthetic histories, against a trivial-rule baseline.

    uv run python evals/detectors.py            # CI: results reproduce
    uv run python evals/detectors.py --write    # refresh (see gate.py)

The candidate is `engine.detectors` (#147 spikes, #148 price increases and
upcoming renewals) and `engine.shortfall` (#151). A true positive is also scored
on what it says (`evidence_valid`, `impact_consistent`). A detector sees
only what the Ledger holds (`Row`), never the ground truth, and returns
dated `Detection`s for the whole history. A detection matches an unmatched
planted event of its type and key when its date falls in that type's window
(`WINDOWS`), in days from the event's date: price rises and spikes may be
flagged up to a month after they show, a renewal must be flagged in the five
weeks before it, and a shortfall warning must come before the balance goes
negative.
"""

import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import gate
import synthetic_history as sh
from finance_agent.engine import detectors, forecast, shortfall
from finance_agent.engine.baselines import Insufficient
from finance_agent.engine.forecast import CashFlow
from finance_agent.ledger import Categorisation

RESULTS = Path(__file__).parent / "results" / "detectors_baseline.json"
SHORTFALL_HORIZON = 3  # months: the next payday can fall in the one after next
WINDOWS = {
    "price_rise": (0, 35),
    "category_spike": (0, 40),
    "forgotten_renewal": (-35, 0),
    "payday_shortfall": (-28, -1),
}


class Row(NamedTuple):
    """A Transaction as the Ledger holds it: no ground truth."""

    id: int
    date: date
    merchant: str
    category: sh.Category
    amount: int  # signed pence
    balance: int


class Detection(NamedTuple):
    type: str  # one of synthetic_history.EVENT_TYPES
    date: date
    key: str  # Merchant, Category label, or "" for a shortfall
    impact: int | None = None  # pence; the trivial rules give none
    evidence: tuple[int, ...] = ()  # Transaction ids


class Detector(Protocol):
    def __call__(self, rows: Sequence[Row], /) -> Sequence[Detection]: ...


def view(h: sh.History) -> list[Row]:
    return [
        Row(t.id, t.date, t.merchant, t.category, t.amount, t.balance) for t in h.txns
    ]


# --- matching -----------------------------------------------------------------


def match(
    events: Sequence[sh.Event], found: Sequence[Detection]
) -> dict[str, tuple[int, int, int]]:
    """(true positives, false positives, false negatives) per type."""
    return pair(events, found)[0]


def pair(
    events: Sequence[sh.Event], found: Sequence[Detection]
) -> tuple[dict[str, tuple[int, int, int]], list[tuple[sh.Event, Detection]]]:
    """`match`'s counts, and each true positive's event and detection."""
    tp: defaultdict[str, int] = defaultdict(int)
    fn: defaultdict[str, int] = defaultdict(int)
    hits: list[tuple[sh.Event, Detection]] = []
    left = list(found)
    for e in sorted(events, key=lambda e: e.date):
        lo, hi = WINDOWS[e.type]
        hit = next(
            (
                d
                for d in sorted(left, key=lambda d: d.date)
                if d.type == e.type
                and d.key == e.key
                and lo <= (d.date - e.date).days <= hi
            ),
            None,
        )
        if hit is None:
            fn[e.type] += 1
        else:
            tp[e.type] += 1
            hits.append((e, hit))
            left.remove(hit)
    fp: defaultdict[str, int] = defaultdict(int)
    for d in left:
        fp[d.type] += 1
    types = sorted(tp.keys() | fp.keys() | fn.keys())
    return {t: (tp[t], fp[t], fn[t]) for t in types}, hits


# --- the trivial-rule baseline and the stub -----------------------------------


def _price_rises(rows: Sequence[Row]) -> list[Detection]:
    """A charge larger than the same Merchant's charge 25-35 days before."""
    out: list[Detection] = []
    last: dict[str, Row] = {}
    for r in rows:
        if r.amount >= 0:
            continue
        prev = last.get(r.merchant)
        if prev and 25 <= (r.date - prev.date).days <= 35 and r.amount < prev.amount:
            out.append(Detection("price_rise", r.date, r.merchant))
        last[r.merchant] = r
    return out


def _spikes(rows: Sequence[Row]) -> list[Detection]:
    """A month over 1.5x the Category's mean of the three months before."""
    spend: defaultdict[sh.Category, defaultdict[int, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for r in rows:
        if r.amount < 0 and r.category != sh.NONE:
            spend[r.category][sh.month_index(r.date)] -= r.amount
    if not rows:
        return []
    months = range(sh.month_index(rows[0].date), sh.month_index(rows[-1].date) + 1)
    out: list[Detection] = []
    for cat, by_month in spend.items():
        for m in months[3:]:
            before = sum(by_month[m - i] for i in (1, 2, 3))
            if 2 * by_month[m] > before:  # > 1.5 x before / 3
                out.append(
                    Detection("category_spike", sh.month_start(m), sh.label(cat))
                )
    return out


def _renewals(rows: Sequence[Row]) -> list[Detection]:
    """A Merchant charged at most once in any 300 days renews 358 days on."""
    dates: defaultdict[str, list[date]] = defaultdict(list)
    for r in rows:
        if r.amount < 0:
            dates[r.merchant].append(r.date)
    return [
        Detection("forgotten_renewal", d + timedelta(days=358), m)
        for m, ds in dates.items()
        if all((b - a).days >= 300 for a, b in zip(ds, ds[1:], strict=False))
        for d in ds
    ]


def _shortfalls(rows: Sequence[Row]) -> list[Detection]:
    """Once per pay cycle: the balance below the last 30 days' spending rate
    (transfers aside) times the days until 30 days after the last credit."""
    out: list[Detection] = []
    credit: date | None = None
    flagged: date | None = None
    for i, r in enumerate(rows):
        if r.amount > 0:
            credit = r.date
            continue
        if credit is None or flagged == credit:
            continue
        spent = -sum(
            x.amount
            for x in rows[: i + 1]
            if x.amount < 0
            and x.category != sh.NONE  # not transfers
            and (r.date - x.date).days < 30
        )
        days_left = (credit + timedelta(days=30) - r.date).days
        if r.balance * 30 < spent * days_left:
            out.append(Detection("payday_shortfall", r.date, ""))
            flagged = credit
    return out


def baseline(rows: Sequence[Row]) -> list[Detection]:
    return _price_rises(rows) + _spikes(rows) + _renewals(rows) + _shortfalls(rows)


# --- the candidate: the Engine's detectors (#147, #148) ----------------------

TYPES = {"price_increase": "price_rise", "upcoming_renewal": "forgotten_renewal"}


def candidate(rows: Sequence[Row]) -> list[Detection]:
    return _detected(rows) + shortfalls(rows)


def _detected(rows: Sequence[Row]) -> list[Detection]:
    """`engine.detectors` on the Ledger's Spending: salary and transfers (no
    Category) are not Spending."""
    found = detectors.detect(
        [
            detectors.Row(r.id, r.date, r.merchant, r.category, r.amount)
            for r in rows
            if r.category != sh.NONE
        ]
    )
    return [
        Detection(TYPES.get(i.type, i.type), i.on, i.key, i.impact, i.evidence)
        for i in found
        if TYPES.get(i.type, i.type) in WINDOWS  # recurring has its own eval
    ]


class _Txn(NamedTuple):
    """A Row as `forecast.project` reads a Transaction."""

    posted_on: date
    amount: Decimal
    description: str
    categorisation: Categorisation | None


def _txns(rows: Sequence[Row]) -> list[_Txn]:
    """Salary is Income and the sweep a Transfer (no Category); Merchant ids
    in order of first appearance."""
    ids: dict[str, int] = {}
    out: list[_Txn] = []
    for r in rows:
        if r.category == sh.NONE:
            kind, cat, mid = ("Income" if r.amount > 0 else "Transfer"), None, None
        else:
            kind, cat = ("Spending" if r.amount < 0 else "Refund"), r.category
            mid = ids.setdefault(r.merchant, len(ids) + 1)
        c = Categorisation(kind, r.merchant, cat, "default", 1, mid)
        out.append(_Txn(r.date, Decimal(r.amount) / 100, r.merchant, c))
    return out


def shortfalls(rows: Sequence[Row]) -> list[Detection]:
    """`engine.shortfall` run at the end of each day with rows, on the balance
    then, the cash flow forecast from that month and the pace as of the
    latest credit; once per payday. Both change only at those, so are cached."""
    txns = _txns(rows)
    flows: dict[date, CashFlow] = {}
    paces: dict[tuple[date, date | None], shortfall.Pace | Insufficient] = {}
    out: list[Detection] = []
    raised: set[str] = set()
    credit: date | None = None
    for i, r in enumerate(rows):
        if r.amount > 0 and r.category == sh.NONE:
            credit = r.date
        if i + 1 < len(rows) and rows[i + 1].date == r.date:
            continue  # the day's closing balance
        start = r.date.replace(day=1)
        if start not in flows:
            flows[start] = forecast.project(txns, {}, start, SHORTFALL_HORIZON)
        if (start, credit) not in paces:
            paces[start, credit] = shortfall.pace(txns, flows[start], r.date)
        spend = paces[start, credit]
        found = shortfall.shortfall(r.balance, r.date, flows[start], spend)
        if isinstance(found, detectors.Insight) and found.key not in raised:
            raised.add(found.key)
            out.append(Detection("payday_shortfall", r.date, "", found.impact))
    return out


MODELS: dict[str, tuple[str, str, Detector]] = {
    "baseline": ("trivial-rules", "1", baseline),
    "candidate": ("engine-detectors", "3", candidate),
}


# --- what a hit says ------------------------------------------------------------


def evidence_valid(e: sh.Event, d: Detection) -> bool:
    """The Insight cites Transactions, and only the planted event's own."""
    return bool(d.evidence) and set(d.evidence) <= set(e.evidence)


def impact_consistent(e: sh.Event, d: Detection) -> bool:
    """A price rise (annualised) and a renewal (the fee) are known exactly, to
    within 5%. A spike is flagged on the day it crosses, so its impact is the
    excess so far: positive, and at most half again the planted excess (the
    baseline's normal is an estimate of the planted one). A shortfall's is a
    projected deficit: within a quarter of the one that came."""
    if d.impact is None:
        return False
    if e.type == "category_spike":
        return 0 < d.impact and 2 * d.impact <= 3 * e.impact
    if e.type == "payday_shortfall":
        return abs(d.impact - e.impact) * 4 <= e.impact
    return abs(d.impact - e.impact) * 20 <= e.impact


# --- the harness --------------------------------------------------------------


def _ratio(a: int, b: int) -> float:
    return round(a / b, 6) if b else 0.0


def evaluate() -> dict[str, Any]:
    histories = sh.generate(sh.SEED)
    models: dict[str, Any] = {}
    for key, (name, version, detect) in MODELS.items():
        totals = {t: [0, 0, 0] for t in sh.EVENT_TYPES}
        hits: list[tuple[sh.Event, Detection]] = []
        for h in histories:
            end = h.txns[-1].date
            found = [d for d in detect(view(h)) if d.date <= end]
            counted, paired = pair(h.events, found)
            hits += paired
            for t, counts in counted.items():
                totals[t] = [a + b for a, b in zip(totals[t], counts, strict=True)]
        totals["overall"] = [sum(c[i] for c in totals.values()) for i in range(3)]
        metrics: dict[str, float] = {}
        for t, (tp, fp, fn) in sorted(totals.items()):
            metrics[f"{t}.precision"] = _ratio(tp, tp + fp)
            metrics[f"{t}.recall"] = _ratio(tp, tp + fn)
        # of the true positives, the share whose evidence and impact hold up
        for t in sh.EVENT_TYPES:
            mine = [(e, d) for e, d in hits if e.type == t]
            n = len(mine)
            metrics[f"{t}.evidence_valid"] = _ratio(
                sum(evidence_valid(e, d) for e, d in mine), n
            )
            metrics[f"{t}.impact_consistent"] = _ratio(
                sum(impact_consistent(e, d) for e, d in mine), n
            )
        models[key] = {
            "model": name,
            "model_version": version,
            "metrics": metrics,
            "counts": {
                t: dict(zip(("tp", "fp", "fn"), c, strict=True))
                for t, c in sorted(totals.items())
            },
        }
    return {
        "harness": "detectors",
        "dataset": "synthetic_history",
        "dataset_version": sh.DATASET_VERSION,
        "dataset_sha256": sh.fingerprint(histories),
        "seed": sh.SEED,
        "histories": len(histories),
        "months": sh.MONTHS,
        "events": {
            t: sum(e.type == t for h in histories for e in h.events)
            for t in sh.EVENT_TYPES
        },
        "windows_days": WINDOWS,
        "models": models,
    }


def lower_is_better(name: str) -> bool:
    return False


if __name__ == "__main__":
    sys.exit(gate.run("detectors", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
