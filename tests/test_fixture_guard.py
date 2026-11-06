import os
import random
import shutil
import string
import subprocess
import sys
from pathlib import Path

import pytest

from fixture_guard import find_violations

OUTSIDE = "data file outside tests/fixtures/synthetic/"


def write(root: Path, rel: str, content: bytes) -> str:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return rel


def test_csv_outside_synthetic_folder_is_rejected(tmp_path: Path) -> None:
    rel = write(tmp_path, "data/statement.csv", b"date,amount\n")
    assert find_violations(tmp_path, [rel]) == [f"{rel}: {OUTSIDE}"]


def test_synthetic_fixture_with_header_is_accepted(tmp_path: Path) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/a.csv", b"# synthetic\ndate\n")
    assert find_violations(tmp_path, [rel]) == []


def test_synthetic_fixture_without_header_is_rejected(tmp_path: Path) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/a.csv", b"date,amount\n")
    assert find_violations(tmp_path, [rel]) == [
        f"{rel}: synthetic fixture must start with '# synthetic'"
    ]


SQLITE_MAGIC = b"SQLite format 3\x00" + b"\x00" * 84


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("STATEMENT.CSV", b"date,amount\n"),
        ("export.Csv", b"date,amount\n"),
        ("statement.csv.bak", b"date,amount\n"),
        ("statement.tsv", b"date\tamount\n"),
        ("ledger.db", SQLITE_MAGIC),
        ("ledger.sqlite", SQLITE_MAGIC),
        ("ledger.SQLITE3", SQLITE_MAGIC),
        ("notes.txt", SQLITE_MAGIC),  # renamed SQLite, caught by magic bytes
        ("innocent", SQLITE_MAGIC),
        ("src/tests/fixtures/synthetic/a.csv", b"# synthetic\n"),  # nested lookalike
        ("tests/fixtures/synthetic_evil/a.csv", b"# synthetic\n"),
        ("tests/fixtures/Synthetic/a.csv", b"# synthetic\n"),
        ("tests/fixtures/synthetic/../../../data/a.csv", b"# synthetic\n"),
    ],
)
def test_bypass_attempts_are_rejected(tmp_path: Path, rel: str, content: bytes) -> None:
    write(tmp_path, rel, content)
    assert find_violations(tmp_path, [rel]) == [f"{rel}: {OUTSIDE}"]


def test_nested_folder_inside_synthetic_is_accepted(tmp_path: Path) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/m1/a.csv", b"# synthetic\nx\n")
    assert find_violations(tmp_path, [rel]) == []


def test_sqlite_inside_synthetic_without_header_is_rejected(tmp_path: Path) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/a.db", SQLITE_MAGIC)
    assert find_violations(tmp_path, [rel]) == [
        f"{rel}: synthetic fixture must start with '# synthetic'"
    ]


def test_header_must_be_exact_first_line(tmp_path: Path) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/a.csv", b"date\n# synthetic\n")
    assert find_violations(tmp_path, [rel]) != []


def test_ordinary_source_files_are_accepted(tmp_path: Path) -> None:
    rels = [
        write(tmp_path, "src/x.py", b"print(1)\n"),
        write(tmp_path, "README.md", b"hi"),
    ]
    assert find_violations(tmp_path, rels) == []


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fixture_guard.py"


def run_guard(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT)], cwd=repo, capture_output=True, text=True
    )


def test_command_fails_on_planted_csv_in_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # noqa: S607
    write(tmp_path, "README.md", b"ok")
    assert run_guard(tmp_path).returncode == 0
    write(tmp_path, "deep/nested/Statement.CSV", b"date,amount\n")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "deep/nested/Statement.CSV" in result.stderr


def test_gitleaks_fails_on_planted_fake_secret(tmp_path: Path) -> None:
    gitleaks = shutil.which("gitleaks")
    if gitleaks is None:
        if os.environ.get("CI"):
            pytest.fail("gitleaks must be installed in CI")
        pytest.skip("gitleaks not installed")
    # Built at runtime so this file holds no secret-shaped literal.
    rng = random.Random(0)  # noqa: S311 - fake token, determinism wanted
    token = "ghp_" + "".join(rng.choices(string.ascii_letters + string.digits, k=36))
    write(tmp_path, "config.py", f'GITHUB_TOKEN = "{token}"\n'.encode())
    result = subprocess.run(  # noqa: S603
        [gitleaks, "dir", str(tmp_path), "--no-banner", "--redact"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
