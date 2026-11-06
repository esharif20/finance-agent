"""Fixture guard: refuse data files that could hold real financial data.

Data files (CSV/TSV/SQLite, by any extension in the name or by SQLite magic bytes)
may only live under tests/fixtures/synthetic/, and every file there must start
with the line `# synthetic`.

Run locally with `uv run python scripts/fixture_guard.py`.
"""

import posixpath
import subprocess
import sys
from pathlib import Path

SYNTHETIC_DIR = "tests/fixtures/synthetic/"
HEADER = b"# synthetic"
SQLITE_MAGIC = b"SQLite format 3\x00"
DATA_SUFFIXES = {".csv", ".tsv", ".db", ".sqlite", ".sqlite3"}


def _is_data_file(path: Path) -> bool:
    if any(s.lower() in DATA_SUFFIXES for s in path.suffixes):
        return True
    with path.open("rb") as f:
        return f.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC


def find_violations(root: Path, paths: list[str]) -> list[str]:
    violations: list[str] = []
    for rel in paths:
        path = root / rel
        if not path.is_file():  # deleted in the working tree
            continue
        if posixpath.normpath(rel).startswith(SYNTHETIC_DIR):
            with path.open("rb") as f:
                first_line = f.readline().rstrip(b"\r\n")
            if first_line != HEADER:
                violations.append(
                    f"{rel}: synthetic fixture must start with '# synthetic'"
                )
        elif _is_data_file(path):
            violations.append(f"{rel}: data file outside {SYNTHETIC_DIR}")
    return violations


def main() -> int:
    root = Path(
        subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )
    # Tracked plus untracked-but-not-ignored, so it also catches files before commit.
    listed = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],  # noqa: S607
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    paths = sorted({p for p in listed.decode().split("\0") if p})
    violations = find_violations(root, paths)
    for v in violations:
        print(v, file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
