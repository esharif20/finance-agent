"""The results gate shared by the M5 harnesses (#146).

    uv run python evals/<harness>.py                          # CI check
    uv run python evals/<harness>.py --write                  # refresh results
    uv run python evals/<harness>.py --write --accept-lower   # lower a score

CI demands that the committed results equal a fresh run, so they are always
reproducible from the seed. `--write` refuses to lower any committed metric of
any model, or to change the dataset without bumping its version, unless
`--accept-lower` says so explicitly (the #119 verifier's lesson on #113).

Results: {"dataset_version", "dataset_sha256", ..., "models": {key: {"model",
"model_version", "metrics": {name: number}, ...}}}. Only "metrics" is gated.
"""

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, cast

TOLERANCE = 1e-9

type Results = dict[str, Any]


def lowered(
    fresh: Results, committed: Results, lower_is_better: Callable[[str], bool]
) -> list[str]:
    """Why `fresh` would lower `committed`; [] when it would not."""
    new_data = fresh["dataset_sha256"] != committed["dataset_sha256"]
    bumped = fresh["dataset_version"] > committed["dataset_version"]
    if new_data and bumped:
        return []  # a new dataset version sets a new bar
    problems: list[str] = []
    if new_data:
        problems.append("dataset changed without a dataset_version bump")
    if fresh["dataset_version"] < committed["dataset_version"]:
        problems.append("dataset_version moved backwards")
    for key, old in committed["models"].items():
        new = fresh["models"].get(key)
        if new is None:
            problems.append(f"model {key} removed")
            continue
        for name, was in old["metrics"].items():
            now = new["metrics"].get(name)
            if now is None:
                problems.append(f"{key} {name} removed")
            elif (now - was if lower_is_better(name) else was - now) > TOLERANCE:
                problems.append(f"{key} {name} {now} worse than {was}")
    return problems


def _normal(results: Results) -> Results:
    return json.loads(json.dumps(results))


def differences(
    fresh: Any, committed: Any, tolerance: float, at: str = ""
) -> list[str]:
    """Where `fresh` differs from `committed`. Two floats may differ by up to
    `tolerance`; everything else (text, integers, structure) must be equal."""
    if isinstance(fresh, dict) and isinstance(committed, dict):
        new, old = cast(Results, fresh), cast(Results, committed)
        return [
            d
            for k in sorted(new.keys() | old.keys())
            for d in differences(new.get(k), old.get(k), tolerance, f"{at}/{k}")
        ]
    if isinstance(fresh, float) and isinstance(committed, float):
        if abs(fresh - committed) <= tolerance:
            return []
    elif fresh == committed:
        return []
    return [f"{at or '/'}: {committed!r} -> {fresh!r}"]


def run(
    name: str,
    evaluate: Callable[[], Results],
    path: Path,
    lower_is_better: Callable[[str], bool],
    argv: Sequence[str],
    tolerance: float = 0.0,
) -> int:
    """`tolerance` is for a harness whose model fits in float, where another
    platform's arithmetic can move a score in its last digits (#149)."""
    fresh = _normal(evaluate())
    if "--write" in argv:
        problems = (
            lowered(fresh, json.loads(path.read_text()), lower_is_better)
            if path.exists()
            else []
        )
        for p in problems:
            print(f"{name} eval: {p}", file=sys.stderr)
        if problems and "--accept-lower" not in argv:
            print(f"{name} eval: not written; pass --accept-lower", file=sys.stderr)
            return 1
        path.write_text(json.dumps(fresh, indent=2) + "\n")
    diffs = differences(fresh, json.loads(path.read_text()), tolerance)
    if diffs:
        for d in diffs[:20]:
            print(f"{name} eval: {d}", file=sys.stderr)
        print(
            f"{name} eval: {path.name} is not a fresh run; use --write", file=sys.stderr
        )
        return 1
    print(f"{name} eval: {path.name} reproduced")
    return 0
