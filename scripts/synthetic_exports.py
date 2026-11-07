"""Write synthetic Lloyds exports (current and saver) into OUT_DIR and print their
paths, for the smoke test to `fa import` into a temp ledger.

    uv run python scripts/synthetic_exports.py OUT_DIR

OUT_DIR must be outside any repository: data files never sit in a checkout.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
from synthetic_lloyds import generate_histories, render_export  # noqa: E402

ROWS = 140  # per file, under the generator's 150-row cap


def write(out: Path) -> list[Path]:
    if any((p / ".git").exists() for p in out.resolve().parents):
        raise SystemExit("synthetic_exports: refusing to write inside a repository")
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for i, h in enumerate(generate_histories(seed=8, days=120)):
        n = len(h.txns)
        for j, start in enumerate(range(0, n, ROWS)):
            f = out / f"synthetic_{i}_{j}.csv"
            f.write_text(render_export(h, start, min(start + ROWS, n)).text)
            files.append(f)
    return files


if __name__ == "__main__":
    for f in write(Path(sys.argv[1])):
        print(f)
