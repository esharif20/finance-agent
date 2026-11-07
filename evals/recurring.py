"""Recurring-detection evaluation (#104, docs/ML_EVALUATIONS.md): a seeded
synthetic set of charge schedules, the "same amount ± 5%, same interval"
baseline, and the regression gate pytest runs.

    uv run python evals/recurring.py          # evaluate; exit 1 on regression
    uv run python evals/recurring.py --write  # regenerate the results

The dataset is the seeded generator below (versioned by DATASET_VERSION);
a schedule is gold-positive when it is a real Subscription or Bill.
"""

import json
import random
import sys
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent.categorise.recurring import detect_recurring

SEED = 104
DATASET_VERSION = 2
PER_CASE = 40
HERE = Path(__file__).parent
RESULTS = HERE / "results" / "recurring_baseline.json"

type Charges = list[tuple[date, int]]
type Case = tuple[str, bool, Charges]  # (case, gold recurring, charges)


def _months(rnd: random.Random, n: int, jitter: int) -> list[date]:
    y, m, d = 2024, rnd.randint(1, 12), rnd.randint(1, 28)
    out: list[date] = []
    for i in range(n):
        k = m - 1 + i
        out.append(
            date(y + k // 12, k % 12 + 1, d) + timedelta(rnd.randint(-jitter, jitter))
        )
    return out


def generate(seed: int = SEED) -> list[Case]:
    rnd = random.Random(seed)  # noqa: S311 - reproducible synthetic data, not security
    start = date(2024, 1, 1)

    def amt() -> int:
        return rnd.randint(199, 9999)

    def steady(n: int, a: int, pct: int = 0) -> list[int]:
        return [a + a * rnd.randint(-pct, pct) // 100 for _ in range(n)]

    makers: dict[str, tuple[bool, Callable[[], Charges]]] = {}

    def case(name: str, gold: bool) -> Callable[[Callable[[], Charges]], None]:
        def put(f: Callable[[], Charges]) -> None:
            makers[name] = (gold, f)

        return put

    @case("monthly", True)
    def _() -> Charges:
        n = rnd.randint(3, 12)
        return list(zip(_months(rnd, n, 0), steady(n, amt()), strict=True))

    @case("monthly_jitter", True)
    def _() -> Charges:
        n = rnd.randint(3, 12)
        return list(zip(_months(rnd, n, 3), steady(n, amt(), 3), strict=True))

    @case("weekly", True)
    def _() -> Charges:
        n = rnd.randint(3, 20)
        ds = [start + timedelta(weeks=i, days=rnd.randint(-1, 1)) for i in range(n)]
        return list(zip(ds, steady(n, amt()), strict=True))

    @case("annual", True)
    def _() -> Charges:
        n = rnd.randint(3, 5)
        ds = [date(2020 + i, 3, 1) + timedelta(rnd.randint(-5, 5)) for i in range(n)]
        return list(zip(ds, steady(n, amt()), strict=True))

    @case("skipped_month", True)
    def _() -> Charges:
        n = rnd.randint(5, 12)
        ds = _months(rnd, n, 2)
        del ds[rnd.randint(1, n - 2)]
        return list(zip(ds, steady(len(ds), amt()), strict=True))

    @case("price_rise", True)
    def _() -> Charges:
        n, a = rnd.randint(4, 12), amt()
        cut = rnd.randint(1, n - 1)
        new = a + a * rnd.randint(10, 30) // 100
        return list(zip(_months(rnd, n, 1), [a] * cut + [new] * (n - cut), strict=True))

    @case("irregular", False)
    def _() -> Charges:
        n, d = rnd.randint(3, 12), start
        out: Charges = []
        for _ in range(n):
            d += timedelta(rnd.randint(1, 60))
            out.append((d, amt()))
        return out

    @case("one_off", False)
    def _() -> Charges:
        return [
            (start + timedelta(rnd.randint(0, 700)), amt())
            for _ in range(rnd.randint(1, 2))
        ]

    @case("monthly_varying_amounts", False)
    def _() -> Charges:  # a regular shop visit, not a subscription
        n = rnd.randint(3, 12)
        return list(zip(_months(rnd, n, 3), [amt() for _ in range(n)], strict=True))

    @case("drift", False)
    def _() -> Charges:
        n, a = rnd.randint(5, 12), amt()
        return list(
            zip(
                _months(rnd, n, 1),
                [a + a * 4 * i // 100 for i in range(n)],
                strict=True,
            )
        )

    @case("fixed_price_irregular", False)
    def _() -> Charges:  # a café visit at one price: some runs look weekly
        n, a, d = rnd.randint(4, 20), amt(), start
        out: Charges = []
        for _ in range(n):
            d += timedelta(rnd.randint(3, 12))
            out.append((d, a))
        return out

    return [
        (name, gold, f()) for name, (gold, f) in makers.items() for _ in range(PER_CASE)
    ]


def baseline(charges: Charges) -> bool:
    """Same amount ± 5% of the first charge; same interval (± 1 day) as the
    first gap."""
    rows = sorted(charges)
    if len(rows) < 3:
        return False
    gaps = [(b[0] - a[0]).days for a, b in zip(rows, rows[1:], strict=False)]
    a0 = rows[0][1]
    return (
        gaps[0] > 0
        and all(abs(g - gaps[0]) <= 1 for g in gaps)
        and all(abs(a - a0) * 100 <= a0 * 5 for _, a in rows)
    )


def detector(charges: Charges) -> bool:
    return detect_recurring(charges) is not None


def _score(cases: Sequence[Case], predict: Callable[[Charges], bool]) -> dict[str, Any]:
    pred = [predict(c) for _, _, c in cases]
    tp = sum(p and g for p, (_, g, _) in zip(pred, cases, strict=True))
    fp = sum(p and not g for p, (_, g, _) in zip(pred, cases, strict=True))
    pos = sum(g for _, g, _ in cases)
    by_case: dict[str, float] = {}
    for name in dict.fromkeys(n for n, _, _ in cases):
        hits = [p for p, (n, _, _) in zip(pred, cases, strict=True) if n == name]
        by_case[name] = round(sum(hits) / len(hits), 6)
    return {
        "precision": round(tp / (tp + fp), 6) if tp + fp else 0.0,
        "recall": round(tp / pos, 6) if pos else 0.0,
        "flagged_rate_by_case": by_case,
    }


def evaluate(cases: Sequence[Case] | None = None) -> dict[str, Any]:
    cases = generate() if cases is None else cases
    return {
        "dataset_version": DATASET_VERSION,
        "seed": SEED,
        "schedules": len(cases),
        "detector": _score(cases, detector),
        "baseline": _score(cases, baseline),
    }


def regressions(fresh: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    out = [
        f"{k} changed"
        for k in ("dataset_version", "seed", "schedules")
        if fresh[k] != committed[k]
    ]
    for m in ("precision", "recall"):
        if fresh["detector"][m] < committed["detector"][m]:
            out.append(f"detector {m} fell")
        if fresh["detector"][m] < fresh["baseline"][m]:
            out.append(f"detector {m} below baseline")
    return out


def main(argv: Sequence[str]) -> int:
    fresh = evaluate()
    if "--write" in argv:
        RESULTS.write_text(json.dumps(fresh, indent=2) + "\n")
        return 0
    problems = regressions(fresh, json.loads(RESULTS.read_text()))
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
