"""Monthly story evaluation (#266, docs/ML_EVALUATIONS.md): cause attribution,
coverage of planted events and exact figures, on `synthetic_history` months
with planted Merchant spikes.

    uv run python evals/story.py            # CI: results reproduce
    uv run python evals/story.py --write    # refresh (see gate.py)

Dataset: the six seeded `synthetic_history` users (read-only), plus
`PER_HISTORY` planted Merchant spikes each: in a discretionary Category-month
with six months behind it and no planted Category spike, one of the
Category's own Merchants takes 1-3 extra charges totalling 100-200% of the
month's seasonal normal. That Merchant is the planted cause.

Metrics, over the recaps of every month holding a planted event:

- `cause.precision`: of the drivers named under a planted Merchant spike's
  Category, the share that are its planted Merchant;
- `cause.recall`: planted Merchant spikes whose planted Merchant is named;
- `coverage`: planted spikes (Merchant and `synthetic_history` Category spikes)
  whose Category is among the month's changes;
- `anomaly_coverage`: planted price rises raised as a price increase in their
  month's anomalies, and forgotten renewals named in their month's anomalies
  or raised as due in the month before's;
- `figures_exact`: changes and drivers whose spend equals a recomputation
  straight from the history's Transactions (must be 1);
- `changes_per_month`: changes listed a month (lower is better: coverage
  bought by listing everything is not coverage).

- **baseline**: the top 3 Categories by absolute change from the previous
  month, each with the one Merchant that moved most the same way; no
  anomalies.
- **candidate**: `engine.story.recap`.
"""

import hashlib
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import gate
import synthetic_history as sh
from finance_agent.engine import story
from finance_agent.engine.detectors import Row

RESULTS = Path(__file__).parent / "results" / "story.json"
SEED = 266
DATASET_VERSION = 1
PER_HISTORY = 3

type Model = Callable[[Sequence[Row], str], story.Recap]


@dataclass(frozen=True)
class Plant:
    month: int
    category: sh.Category
    merchant: str


@dataclass(frozen=True)
class Case:
    history: sh.History
    rows: tuple[Row, ...]
    plants: tuple[Plant, ...]


def _month(m: int) -> str:
    return f"{sh.month_start(m):%Y-%m}"


def build(seed: int = SEED) -> list[Case]:
    rng = random.Random(seed)  # noqa: S311 - a seeded dataset, not a secret
    cases: list[Case] = []
    for h in sh.generate():
        rows = [
            Row(t.id, t.date, t.merchant, t.category, t.amount)
            for t in h.txns
            if t.category != sh.NONE
        ]
        spiked = {(e.category, e.month) for e in h.events if e.type == "category_spike"}
        free = [
            (c, m)
            for c in sorted(sh.DISCRETIONARY)
            for m in range(sh.BASELINE_MONTHS, h.months)
            if (c, m) not in spiked
        ]
        plants: list[Plant] = []
        next_id = max(t.id for t in h.txns) + 1
        for c, m in rng.sample(free, PER_HISTORY):
            merchant = rng.choice(sh.DISCRETIONARY[c][2])
            extra = sh.expected_spend(h, c, m) * rng.randint(100, 200) // 100
            n = rng.randint(1, 3)
            parts = [extra // n] * n
            parts[0] += extra - sum(parts)
            for p in parts:
                d = sh.month_start(m).replace(day=rng.randint(1, 28))
                rows.append(Row(next_id, d, merchant, c, -p))
                next_id += 1
            plants.append(Plant(m, c, merchant))
        cases.append(Case(h, tuple(rows), tuple(plants)))
    return cases


def baseline(rows: Sequence[Row], month: str) -> story.Recap:
    first, last = story.bounds(month)
    prev_last = first - timedelta(days=1)
    prev_first = prev_last.replace(day=1)

    def spend(rs: Sequence[Row], a: date, b: date) -> int:
        return max(0, -sum(r.amount for r in rs if a <= r.posted_on <= b))

    by_cat: dict[sh.Category, list[Row]] = {}
    for r in rows:
        by_cat.setdefault(r.category, []).append(r)
    changes: list[story.Change] = []
    for c, rs in by_cat.items():
        now, before = spend(rs, first, last), spend(rs, prev_first, prev_last)
        delta = now - before
        movers: list[story.Driver] = []
        for merchant in sorted({r.merchant for r in rs}):
            ms = [r for r in rs if r.merchant == merchant]
            m_now, m_before = spend(ms, first, last), spend(ms, prev_first, prev_last)
            if (m_now - m_before) * delta > 0:
                movers.append(story.Driver(merchant, m_now, m_before, m_now - m_before))
        movers.sort(key=lambda d: (-abs(d.change), d.merchant))
        changes.append(
            story.Change(sh.label(c), now, before, delta, False, tuple(movers[:1]))
        )
    changes.sort(key=lambda c: (-abs(c.change), c.category))
    return story.Recap(month, tuple(changes[:3]), ())


def candidate(rows: Sequence[Row], month: str) -> story.Recap:
    return story.recap(rows, month)


def score(cases: Sequence[Case], model: Model) -> dict[str, float]:
    named = right = found = 0
    plants = covered = spikes = 0
    anomalies = raised = 0
    figures = exact = 0
    months = listed = 0
    for case in cases:
        events = [e for e in case.history.events if e.month >= sh.BASELINE_MONTHS]
        wanted = {p.month for p in case.plants} | {e.month for e in events}
        for m in sorted(wanted):
            r = model(case.rows, _month(m))
            months += 1
            listed += len(r.changes)
            first, last = story.bounds(r.month)
            by_label = {c.category: c for c in r.changes}
            for c in r.changes:
                rs = [
                    x
                    for x in case.rows
                    if sh.label(x.category) == c.category
                    and first <= x.posted_on <= last
                ]
                for want, rows in (
                    (c.spent, rs),
                    *(
                        (d.spent, [x for x in rs if x.merchant == d.merchant])
                        for d in c.drivers
                    ),
                ):
                    figures += 1
                    exact += want == max(0, -sum(x.amount for x in rows))
            for p in (p for p in case.plants if p.month == m):
                plants += 1
                spikes += 1
                c = by_label.get(sh.label(p.category))
                if c is None:
                    continue
                covered += 1
                names = [d.merchant for d in c.drivers]
                named += len(names)
                right += names.count(p.merchant)
                found += p.merchant in names
            for e in (e for e in events if e.month == m):
                if e.type == "category_spike":
                    spikes += 1
                    covered += e.key in by_label
                elif e.type == "price_rise":
                    anomalies += 1
                    raised += any(
                        a.type == "price_increase" and a.key == e.key
                        for a in r.anomalies
                    )
                elif e.type == "forgotten_renewal":
                    # named in its month, or flagged as due in the month before
                    before = model(case.rows, _month(m - 1)).anomalies
                    anomalies += 1
                    raised += any(a.key == e.key for a in r.anomalies) or any(
                        a.type == "upcoming_renewal" and a.key == e.key for a in before
                    )
    return {
        "cause.precision": round(right / named, 6) if named else 0.0,
        "cause.recall": round(found / plants, 6),
        "coverage": round(covered / spikes, 6),
        "anomaly_coverage": round(raised / anomalies, 6),
        "figures_exact": round(exact / figures, 6) if figures else 1.0,
        "changes_per_month": round(listed / months, 6),
    }


MODELS: dict[str, tuple[str, str, Model]] = {
    "baseline": ("top3-month-over-month", "1", baseline),
    "candidate": ("engine.story", str(story.VERSION), candidate),
}


def evaluate() -> dict[str, Any]:
    cases = build()
    return {
        "harness": "story",
        "dataset": "synthetic_history + planted Merchant spikes",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(repr(cases).encode()).hexdigest(),
        "seed": SEED,
        "histories": len(cases),
        "planted_merchant_spikes": sum(len(c.plants) for c in cases),
        "models": {
            key: {"model": name, "model_version": version, "metrics": score(cases, m)}
            for key, (name, version, m) in MODELS.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name == "changes_per_month"


if __name__ == "__main__":
    sys.exit(gate.run("story", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
