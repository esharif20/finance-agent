"""Watched-folder auto-import: a launchd WatchPaths agent runs `fa watch run`.

Notifications carry counts and line numbers only (no amounts, names, accounts).
"""

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from finance_agent.cli import LOCAL_USER
from finance_agent.ledger import Ledger
from finance_agent.storage import DB_ENV, open_db

LABEL = "com.finance-agent.watch"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
TITLE = "Finance Agent"

Notifier = Callable[[str, str], None]
Launchctl = Callable[[list[str]], None]

_SCRIPT = (
    "on run argv\n"
    "display notification (item 2 of argv) with title (item 1 of argv)\n"
    "end run"
)


def osascript(title: str, body: str) -> None:
    subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", _SCRIPT, title, body], check=False
    )


def _launchctl(args: list[str]) -> None:
    subprocess.run(["/bin/launchctl", *args], check=False)  # noqa: S603


def run(folder: Path, notify: Notifier = osascript) -> int:
    """Import every CSV in folder via the Ledger; stay silent if nothing changed."""
    files = sorted(p for p in folder.glob("*.csv") if p.is_file())
    if not files:
        return 0
    try:
        engine = open_db()  # raises on a plaintext or wrong-key database
    except (OSError, ValueError):
        notify(TITLE, "Import refused: database unavailable or not encrypted")
        return 1
    try:
        with engine.begin() as conn:
            r = Ledger(conn, LOCAL_USER).import_files(files)
    except (OSError, ValueError):
        notify(TITLE, "Import failed")
        return 1
    finally:
        engine.dispose()
    if r.added == 0 and not r.rejected:
        return 0
    body = (
        f"{r.added} added, {r.already_known} already known, {len(r.rejected)} rejected"
    )
    if r.rejected:
        body += " (line " + ", ".join(str(x.line) for x in r.rejected) + ")"
    notify(TITLE, body)
    return 1 if r.rejected else 0


def write_agent(
    label: str,
    args: list[str],
    when: dict[str, object],
    agents_dir: Path | None = None,
    ctl: Launchctl | None = None,
) -> Path:
    """Write and load a launchd agent that runs `fa <args>`. A custom database
    path (FA_DB_PATH) is passed through so the job opens the same Ledger."""
    agents_dir = agents_dir or AGENTS_DIR
    agents_dir.mkdir(parents=True, exist_ok=True)
    boot = "import sys; from finance_agent.cli import main; sys.exit(main())"
    plist: dict[str, object] = {
        "Label": label,
        "ProgramArguments": [sys.executable, "-c", boot, *args],
        **when,
    }
    if db := os.environ.get(DB_ENV):
        plist["EnvironmentVariables"] = {DB_ENV: db}
    path = agents_dir / f"{label}.plist"
    path.write_bytes(plistlib.dumps(plist))
    (ctl or _launchctl)(["load", str(path)])
    return path


def remove_agent(
    label: str, agents_dir: Path | None = None, ctl: Launchctl | None = None
) -> None:
    path = (agents_dir or AGENTS_DIR) / f"{label}.plist"
    if path.exists():
        (ctl or _launchctl)(["unload", str(path)])
        path.unlink()


def install(
    folder: Path, agents_dir: Path | None = None, ctl: Launchctl | None = None
) -> Path:
    folder = folder.expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    return write_agent(
        LABEL,
        ["watch", "run", str(folder)],
        {"WatchPaths": [str(folder)]},
        agents_dir,
        ctl,
    )


def remove(agents_dir: Path | None = None, ctl: Launchctl | None = None) -> None:
    remove_agent(LABEL, agents_dir, ctl)
