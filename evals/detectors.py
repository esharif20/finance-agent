"""Detector evaluation (#146, docs/ML_EVALUATIONS.md): precision and recall per
planted event type on the synthetic histories, against a trivial-rule baseline.

    uv run python evals/detectors.py            # CI: results reproduce
    uv run python evals/detectors.py --write    # refresh (see gate.py)

The detector tickets implement `Detector` as `candidate`: #147 flags Category
spikes against the personal seasonal baseline; #148 adds the other types.
A detector sees only what the Ledger holds (`Row`), never the ground truth,
and returns dated `Detection`s for the whole history. A detection matches an
unmatched planted event of its type and key when its date falls in that
type's window (`WINDOWS`), in days from the event's date: price rises and
spikes may be flagged up to a month after they show, a renewal must be flagged
in the five weeks before it, and a shortfall warning must come before the
balance goes negative.
"""

import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NamedTuple, Protocol

import gate
import synthetic_history as sh
from finance_agent.engine.baselines import Insufficient, seasonal_baseline

RESULTS = Path(__file__).parent / "results" / "detectors_baseline.json"
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
    tp: defaultdict[str, int] = defaultdict(int)
    fn: defaultdict[str, int] = defaultdict(int)
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
            left.remove(hit)
    fp: defaultdict[str, int] = defaultdict(int)
    for d in left:
        fp[d.type] += 1
    return {t: (tp[t], fp[t], fn[t]) for t in sorted(tp.keys() | fp.keys() | fn.keys())}


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


# --- the candidate ------------------------------------------------------------

SPIKE = (3, 2)  # a month's spend over 1.5 x its personal seasonal baseline


def _baseline_spikes(rows: Sequence[Row]) -> list[Detection]:
    """A Category-month spending over SPIKE x its seasonal baseline (#147),
    flagged on the day it crosses; nothing below the ADR 0015 gate."""
    by_cat: defaultdict[sh.Category, list[Row]] = defaultdict(list)
    for r in rows:
        if r.category != sh.NONE:
            by_cat[r.category].append(r)
    out: list[Detection] = []
    for cat, rs in by_cat.items():
        pairs = [(r.date, r.amount) for r in rs]
        by_month: defaultdict[int, list[Row]] = defaultdict(list)
        for r in rs:
            by_month[sh.month_index(r.date)].append(r)
        for m, month in sorted(by_month.items()):
            b = seasonal_baseline(pairs, sh.month_start(m))
            if isinstance(b, Insufficient) or b.expected == 0:
                continue
            spent = 0
            for r in sorted(month, key=lambda r: (r.date, r.id)):
                spent += max(0, -r.amount)
                if SPIKE[1] * spent > SPIKE[0] * b.expected:
                    out.append(Detection("category_spike", r.date, sh.label(cat)))
                    break
    return out


def candidate(rows: Sequence[Row]) -> list[Detection]:
    return _baseline_spikes(rows)


MODELS: dict[str, tuple[str, str, Detector]] = {
    "baseline": ("trivial-rules", "1", baseline),
    "candidate": ("engine-detectors", "1", candidate),
}


# --- the harness --------------------------------------------------------------


def _ratio(a: int, b: int) -> float:
    return round(a / b, 6) if b else 0.0


def evaluate() -> dict[str, Any]:
    histories = sh.generate(sh.SEED)
    models: dict[str, Any] = {}
    for key, (name, version, detect) in MODELS.items():
        totals = {t: [0, 0, 0] for t in sh.EVENT_TYPES}
        for h in histories:
            end = h.txns[-1].date
            found = [d for d in detect(view(h)) if d.date <= end]
            for t, counts in match(h.events, found).items():
                totals[t] = [a + b for a, b in zip(totals[t], counts, strict=True)]
        totals["overall"] = [sum(c[i] for c in totals.values()) for i in range(3)]
        metrics: dict[str, float] = {}
        for t, (tp, fp, fn) in sorted(totals.items()):
            metrics[f"{t}.precision"] = _ratio(tp, tp + fp)
            metrics[f"{t}.recall"] = _ratio(tp, tp + fn)
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
