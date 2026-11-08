"""`fa doctor`: one line per check, pass or fail, with a fix for each failure.
Keys are checked by length only; the access URL's host is compared, never shown."""

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from finance_agent.assistant.ollama import DEFAULT_CHOICE, MODELS
from finance_agent.categorise.embedding import MODEL as EMBED_MODEL
from finance_agent.cli import backup, keys, models, schedule, watch
from finance_agent.ingest import simplefin
from finance_agent.storage import backup as store
from finance_agent.storage import default_db_path, is_encrypted, keychain

MIN_DISK = 2 * 1024**3
Check = tuple[bool, str, str]  # ok, what, fix


def _launchctl_loaded(label: str) -> bool:
    r = subprocess.run(  # noqa: S603 - fixed argv
        ["/bin/launchctl", "list", label], capture_output=True, check=False
    )
    return r.returncode == 0


@dataclass
class Probes:
    installed: Callable[[], set[str] | None] = field(
        default_factory=lambda: models.LocalOllama().installed
    )
    loaded: Callable[[str], bool] = _launchctl_loaded
    agents_dir: Path = watch.AGENTS_DIR
    disk_free: Callable[[Path], int] = lambda p: shutil.disk_usage(p).free
    choice: Callable[[], str | None] = models.choice


def checks(p: Probes | None = None) -> list[Check]:
    p = p or Probes()
    out: list[Check] = list(keys.check())
    db = default_db_path()
    out.append(
        (
            db.exists() and is_encrypted(db),
            "ledger encrypted (SQLCipher)",
            "run `fa setup` to create it, or `fa restore` from a backup",
        )
    )
    have = p.installed()
    chat = models.CHOICE_NAME.get(p.choice() or DEFAULT_CHOICE, MODELS[DEFAULT_CHOICE])
    out.append(
        (have is not None, "Ollama reachable on localhost", "start it: `ollama serve`")
    )
    if have is not None:
        for m in (chat, EMBED_MODEL):
            out.append((m in have, f"model {m} present", "`fa models install`"))
    access = keychain.get_text(keychain.SIMPLEFIN_ACCESS_SERVICE)
    if access is not None:
        try:
            host = urlsplit(access).hostname
        except ValueError:
            host = None
        out.append(
            (
                host == simplefin.HOST,
                f"bank access pinned to {simplefin.HOST}",
                "reconnect: `fa bank disconnect`, then `fa bank`",
            )
        )
    for label, what, fix in (
        (schedule.LABEL, "daily sync agent loaded", "`fa sync --install`"),
        (backup.LABEL, "backup agent loaded", "`fa backup setup`"),
    ):
        present = (p.agents_dir / f"{label}.plist").exists()
        out.append((present and p.loaded(label), what, fix))
    data = db.parent if db.parent.exists() else Path.home()
    out.append(
        (p.disk_free(data) >= MIN_DISK, "at least 2 GB disk free", "free some space")
    )
    if store.config(db.parent) is None:
        out.append((False, "backup drive registered", "`fa backup setup`"))
    else:
        stale = store.status(db.parent, datetime.now(UTC)).warning
        out.append((not stale, "backup in the last 7 days", "plug in the backup drive"))
    if keychain.gemini_key():  # an explicit check: it may read the Keychain
        keychain.mark_gemini(db, True)  # a key stored before the flag existed
        out.append(
            (
                True,
                "Gemini key stored; its paid tier is checked when the gateway starts",
                "",
            )
        )
    return out


def run(p: Probes | None = None) -> int:
    results = checks(p)
    for ok, what, fix in results:
        print(f"  {'✓' if ok else '✗'} {what}" + ("" if ok else f"  → {fix}"))
    failed = sum(not ok for ok, _, _ in results)
    print(
        f"{failed} problem{'s' if failed != 1 else ''} found."
        if failed
        else "All good."
    )
    return 1 if failed else 0
