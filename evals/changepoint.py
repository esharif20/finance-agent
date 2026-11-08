"""Change-point evaluation (#256, docs/ML_EVALUATIONS.md): detection delay,
false alarms per series-year, and precision and recall, on the seeded series
of `synthetic_life_changes`.

    uv run python evals/changepoint.py            # CI: results reproduce
    uv run python evals/changepoint.py --write    # refresh (see gate.py)

Every detector is online: it reports a shift in the month it would first know
of it, from that month and earlier ones only, and never before the ninth month
of a series (ADR 0015). A planted shift is found by the first report on its
series from its month to `MAX_DELAY` months later; the delay is the months
between. Any other report is a false alarm, and one in the same window after a
one-off spike is a spike alarm too. Precision is per series, the only label a
detector sees (each shift type lives on one series); recall and delay are per
shift type; a type never found scores a delay of MAX_DELAY + 1. False
alarms count every series, groceries too.

- **baseline**: the 3-month mean differs from the 12-month mean by more than
  20% of it (a run of such months is one report);
- **bocpd**: Bayesian online change-point detection (Adams and MacKay 2007)
  on the same clipped robust scores as the CUSUM, Gaussian runs with a normal
  prior on the mean, reporting when most posterior mass is on a run, newer than
  the current level's, that began 3-4 months ago;
- **candidate**: `engine.changepoint`, a clipped two-sided CUSUM.

Both detectors' constants were tuned on a development seed (`DEV_SEED`,
`lc.generate(DEV_SEED)`), never on the reported seed: the best BOCPD there
reached recall 0.86, against the CUSUM's 1.00 with fewer false alarms.
"""

import math
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import gate
import synthetic_history as sh
import synthetic_life_changes as lc
from finance_agent.engine import changepoint as cp
from finance_agent.engine.baselines import Insufficient

RESULTS = Path(__file__).parent / "results" / "changepoint.json"
MAX_DELAY = 6
DEV_SEED = 257
GATE = cp.MIN_MONTHS - 1  # the first month index a report may fall in

type Detector = Callable[[Sequence[int]], list[int]]  # report months


def baseline(values: Sequence[int]) -> list[int]:
    out: list[int] = []
    firing = False
    for t in range(GATE, len(values)):
        short = sum(values[t - 2 : t + 1]) / 3
        window = values[max(0, t - 11) : t + 1]
        long = sum(window) / len(window)
        now = abs(short - long) > 0.2 * long
        if now and not firing:
            out.append(t)
        firing = now
    return out


HAZARD = 1 / 24
PRIOR_VAR = 9.0  # a new run's mean, in clipped robust scores
RECENT = (3, 4)  # run lengths, in months, that count as a fresh change


def bocpd(values: Sequence[int]) -> list[int]:
    out: list[int] = []
    start = 0
    probs: list[float] = []
    sums: list[float] = []
    counts: list[int] = []
    for t, x in enumerate(values):
        reference = values[max(start, t - cp.REFERENCE) : t]
        if len(reference) < cp.MIN_REFERENCE:
            probs, sums, counts = [], [], []
            continue
        m, s = cp.scale(reference)
        z = max(-cp.CLIP, min(cp.CLIP, (x - m) / s))
        if probs:
            pred: list[float] = []
            for total, n in zip(sums, counts, strict=True):
                precision = n + 1 / PRIOR_VAR
                var = 1 + 1 / precision
                mu = total / precision
                pred.append(math.exp(-((z - mu) ** 2) / (2 * var)) / math.sqrt(var))
            joint = [p * q for p, q in zip(probs, pred, strict=True)]
            new = [HAZARD * sum(joint)] + [(1 - HAZARD) * j for j in joint]
        else:
            new = [1.0]
        norm = sum(new)
        probs = [p / norm for p in new]
        sums = [z] + [total + z for total in sums]
        counts = [1] + [n + 1 for n in counts]
        # the oldest run is the current level itself, never a fresh change
        recent = sum(
            p
            for p, n in zip(probs[:-1], counts[:-1], strict=True)
            if RECENT[0] <= n <= RECENT[1]
        )
        if t >= GATE and recent > 0.5:
            out.append(t)
            best = max(range(len(probs)), key=lambda i: probs[i])
            start = t + 1 - counts[best]
            probs, sums, counts = [1.0], [z], [1]
    return out


def candidate_reports(rows: Sequence[cp.Row], series: cp.Series) -> list[int]:
    found = cp.detect_changes(rows, series, sh.month_start(lc.MONTHS - 1))
    if isinstance(found, Insufficient):
        raise TypeError(f"every series has {lc.MONTHS} months")
    return [sh.month_index(c.detected) for c in found]


def _values(h: lc.History, series: cp.Series) -> list[int]:
    _, values, _ = cp.monthly(h.rows[series], series, sh.month_start(h.months - 1))
    return values


MODELS: dict[str, tuple[str, str, Detector | None]] = {
    "baseline": ("mean3-vs-mean12-20pct", "1", baseline),
    "bocpd": ("bocpd-gaussian", "1", bocpd),
    "candidate": ("cusum-clipped", "1", None),  # runs on the rows, end to end
}


def score(histories: Sequence[lc.History], model: Detector | None) -> dict[str, float]:
    found: dict[str, int] = dict.fromkeys(lc.SHIFT_TYPES, 0)
    planted: dict[str, int] = dict.fromkeys(lc.SHIFT_TYPES, 0)
    delays: dict[str, list[int]] = {t: [] for t in lc.SHIFT_TYPES}
    true: dict[str, int] = dict.fromkeys(cp.SERIES, 0)
    reported: dict[str, int] = dict.fromkeys(cp.SERIES, 0)
    false_alarms = spike_alarms = spikes = 0
    for h in histories:
        for series in cp.SERIES:
            reports = (
                candidate_reports(h.rows[series], series)
                if model is None
                else model(_values(h, series))
            )
            reported[series] += len(reports)
            unclaimed = sorted(reports)
            for e in (e for e in h.shifts if e.series == series):
                hit = next(
                    (d for d in unclaimed if e.month <= d <= e.month + MAX_DELAY), None
                )
                if e.type == "spike":
                    spikes += 1
                    spike_alarms += hit is not None
                    continue
                planted[e.type] += 1
                if hit is not None:
                    unclaimed.remove(hit)
                    found[e.type] += 1
                    delays[e.type].append(hit - e.month)
                    true[series] += 1
            false_alarms += len(unclaimed)
    years = len(histories) * len(cp.SERIES) * (lc.MONTHS - GATE) / 12
    metrics: dict[str, float] = {}
    for t in lc.SHIFT_TYPES:
        metrics[f"{t}.recall"] = round(found[t] / planted[t], 6)
        metrics[f"{t}.delay"] = round(
            sum(delays[t]) / len(delays[t]) if delays[t] else MAX_DELAY + 1, 6
        )
    for s in sorted(set(lc.SERIES_OF.values())):  # groceries has no shift type
        metrics[f"{s}.precision"] = round(
            true[s] / reported[s] if reported[s] else 1.0, 6
        )
    every_delay = [d for t in lc.SHIFT_TYPES for d in delays[t]]
    metrics["overall.recall"] = round(sum(found.values()) / sum(planted.values()), 6)
    metrics["overall.precision"] = round(
        sum(true.values()) / max(1, sum(reported.values())), 6
    )
    metrics["overall.delay"] = round(sum(every_delay) / max(1, len(every_delay)), 6)
    metrics["false_alarms_per_series_year"] = round(false_alarms / years, 6)
    metrics["spike_alarm_rate"] = round(spike_alarms / spikes, 6)
    return metrics


def evaluate() -> dict[str, Any]:
    histories = lc.generate()
    counts = {
        t: sum(e.type == t for h in histories for e in h.shifts)
        for t in (*lc.SHIFT_TYPES, "spike")
    }
    return {
        "harness": "changepoint",
        "dataset": "synthetic_life_changes",
        "dataset_version": lc.DATASET_VERSION,
        "dataset_sha256": lc.fingerprint(histories),
        "seed": lc.SEED,
        "histories": len(histories),
        "months": lc.MONTHS,
        "planted": counts,
        "max_delay": MAX_DELAY,
        "models": {
            key: {
                "model": name,
                "model_version": version,
                "metrics": score(histories, m),
            }
            for key, (name, version, m) in MODELS.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name.endswith((".delay", "false_alarms_per_series_year", "spike_alarm_rate"))


if __name__ == "__main__":
    sys.exit(gate.run("changepoint", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
