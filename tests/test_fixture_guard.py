import os
import random
import shutil
import string
import subprocess
import sys
from pathlib import Path

import pytest

from fixture_guard import find_violations

OUTSIDE = "data file outside tests/fixtures/synthetic/ or evals/datasets/"


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


# Verifier adversarial cases (ticket #3).


@pytest.mark.parametrize(
    "content",
    [
        b"\xef\xbb\xbf# synthetic\ndate\n",  # UTF-8 BOM before header
        b" # synthetic\ndate\n",  # leading whitespace
        b"\n# synthetic\ndate\n",  # header on line 2
        b"# synthetic-ish real export\ndate\n",  # header as a prefix only
        b"# SYNTHETIC\ndate\n",
        b"",  # empty file
    ],
)
def test_near_miss_headers_are_rejected(tmp_path: Path, content: bytes) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/a.csv", content)
    assert find_violations(tmp_path, [rel]) == [
        f"{rel}: synthetic fixture must start with '# synthetic'"
    ]


def test_crlf_header_is_accepted(tmp_path: Path) -> None:
    rel = write(tmp_path, "tests/fixtures/synthetic/a.csv", b"# synthetic\r\nx\r\n")
    assert find_violations(tmp_path, [rel]) == []


def test_innocent_named_symlink_to_sqlite_is_rejected(tmp_path: Path) -> None:
    target = write(tmp_path, "elsewhere/blob", SQLITE_MAGIC)
    (tmp_path / "notes.md").symlink_to(tmp_path / target)
    violations = find_violations(tmp_path, ["notes.md"])
    assert violations == [f"notes.md: {OUTSIDE}"]


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # noqa: S603, S607


def test_force_added_gitignored_csv_is_rejected(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    write(tmp_path, ".gitignore", b"*.csv\n")
    write(tmp_path, "real.csv", b"date,amount\n")
    assert run_guard(tmp_path).returncode == 0  # ignored, cannot be committed
    git(tmp_path, "add", "-f", "real.csv")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "real.csv" in result.stderr


def test_sqlite_renamed_and_committed_is_rejected(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    write(tmp_path, "docs/diagram.png", SQLITE_MAGIC)
    git(tmp_path, "add", ".")
    result = run_guard(tmp_path)
    assert result.returncode == 1
    assert "docs/diagram.png" in result.stderr


# Fix pass: content sniffing for CSV, and evals/datasets/ (docs/DATA_POLICY.md).

LLOYDS = (
    b"Transaction Date,Transaction Type,Sort Code,Account Number,"
    b"Transaction Description,Debit Amount,Credit Amount,Balance\n"
    b"01/03/2026,DEB,11-22-33,00000001,SHOP,5.00,,10.00\n"
)


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("notes.txt", LLOYDS),  # renamed export
        ("statement", LLOYDS),  # no extension
        ("export.md", LLOYDS),
        ("statement.txt", b"\xef\xbb\xbf" + LLOYDS),  # BOM
        ("statement.txt", b"# synthetic\n" + LLOYDS),  # header outside the folder
        ("monzo.txt", b"Date,Name,Amount,Currency,Category\n"),
        ("hsbc.txt", b'"Date","Description","Amount"\n'),
        ("tab.txt", b"Date\tDescription\tAmount\tBalance\n"),
        ("semi.txt", b"Date;Payee;Amount\n"),
        ("crlf.txt", LLOYDS.replace(b"\n", b"\r\n")),
        ("utf16.txt", LLOYDS.decode().encode("utf-16")),  # Excel "Unicode text"
        ("utf16be.txt", b"\xfe\xff" + LLOYDS.decode().encode("utf-16-be")),
        ("evals/datasets_evil/a.csv", b"# synthetic\n"),
        ("src/evals/datasets/a.csv", b"# synthetic\n"),
    ],
)
def test_csv_content_is_caught_whatever_the_name(
    tmp_path: Path, rel: str, content: bytes
) -> None:
    write(tmp_path, rel, content)
    assert find_violations(tmp_path, [rel]) == [f"{rel}: {OUTSIDE}"]


@pytest.mark.parametrize(
    "content",
    [
        b"# Title\n\nA note about dates, amounts and balances in prose.\n",
        b"import csv\nHEADER = ('Date', 'Amount')\n",
        b"a,b,c\n1,2,3\n",  # a CSV, but nothing like a bank's columns
        b"",
        b"\x89PNG\r\n\x1a\n\x00\x00",
    ],
)
def test_ordinary_text_is_not_mistaken_for_an_export(
    tmp_path: Path, content: bytes
) -> None:
    rel = write(tmp_path, "docs/thing.txt", content)
    assert find_violations(tmp_path, [rel]) == []


def test_evals_datasets_follow_the_synthetic_header_rule(tmp_path: Path) -> None:
    ok = write(tmp_path, "evals/datasets/cat/a.csv", b"# synthetic\n" + LLOYDS)
    bad = write(tmp_path, "evals/datasets/b.jsonl", b'{"description": "x"}\n')
    assert find_violations(tmp_path, [ok, bad]) == [
        f"{bad}: synthetic fixture must start with '# synthetic'"
    ]


def test_the_repository_itself_passes() -> None:
    assert run_guard(SCRIPT.parents[1]).returncode == 0
