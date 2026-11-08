"""Watched-folder auto-import: a launchd WatchPaths agent runs `fa watch run`.

Notifications carry counts and line numbers only (no amounts, names, accounts).
"""

import os
import plistlib
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from finance_agent import categorise
from finance_agent.categorise import auto, subscription_alerts
from finance_agent.cli import LOCAL_USER
from finance_agent.ledger import Ledger
from finance_agent.notify import TITLE as TITLE
from finance_agent.notify import Notifier as Notifier
from finance_agent.notify import osascript as osascript
from finance_agent.storage import DB_ENV, open_db

LABEL = "com.finance-agent.watch"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

Launchctl = Callable[[list[str]], None]


def _launchctl(args: list[str]) -> None:
    subprocess.run(["/bin/launchctl", *args], check=False)  # noqa: S603


def run(folder: Path, notify: Notifier = osascript) -> int:
    """Import every CSV in folder via the Ledger; stay silent if nothing changed.
    Symlinks and anything resolving outside the folder are skipped."""
    root = folder.resolve()
    files = sorted(
        p
        for p in folder.glob("*.csv")
        if not p.is_symlink() and p.is_file() and p.resolve().is_relative_to(root)
    )
    if not files:
        return 0
    try:
        # never creates a ledger or keys; raises on plaintext, wrong or lost key
        engine = open_db(create=False)
    except (OSError, ValueError, LookupError):
        notify(TITLE, "Import refused: database unavailable or not encrypted")
        return 1
    try:
        with engine.begin() as conn:
            r = Ledger(conn, LOCAL_USER).import_files(files)
            categorise.run(conn, LOCAL_USER)  # same as `fa import`
        auto.refresh_quietly(engine, LOCAL_USER)  # no transaction held
        subscription_alerts.run(engine, LOCAL_USER, notify)  # never raises
    except (OSError, ValueError, LookupError):
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
    if path.is_symlink():
        raise ValueError(f"refusing to replace a symlink at {path}")
    fd, tmp = tempfile.mkstemp(dir=agents_dir, prefix=f".{label}.")
    try:  # written whole, then renamed: launchd never reads half a plist
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(plistlib.dumps(plist))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
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
