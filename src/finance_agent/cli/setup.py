"""`fa setup`: one guided run that resumes where it stopped, and `fa uninstall`.

Steps: database key, bank (Lunch Flow), local AI, optional cloud key,
background jobs, then `fa doctor` and the UI. Each finished step is recorded in
`setup.json` in the data directory (step names only), so a second run skips
it. Secrets come through getpass or `--token-stdin` only, never argv."""

import getpass
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from finance_agent.cli import (
    backup,
    bank,
    doctor,
    keys,
    launcher,
    models,
    schedule,
    watch,
)
from finance_agent.identity import current_user
from finance_agent.storage import DEFAULT_DB, default_db_path, keychain, open_db

STATE = "setup.json"
Ask = Callable[[str], str]


@dataclass
class Ctx:
    ask: Ask = input
    hidden: Ask = getpass.getpass
    open_url: Callable[[str], object] = launcher._open_browser  # pyright: ignore[reportPrivateUsage]
    token: str | None = None  # from --token-stdin
    agents_dir: Path | None = None
    ctl: watch.Launchctl | None = None


def _yes(ask: Ask, prompt: str, default: bool = True) -> bool:
    a = ask(f"{prompt} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return default if not a else a in ("y", "yes")


def _state_path() -> Path:
    return default_db_path().parent / STATE


def done_steps() -> set[str]:
    try:
        raw = json.loads(_state_path().read_text())
    except (OSError, ValueError):
        return set()
    return {s for s in raw.get("done", []) if isinstance(s, str)}


def _mark(step: str) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"done": sorted(done_steps() | {step})}))


def step_key(ctx: Ctx) -> bool:
    open_db(create=True).dispose()  # creates the key only with a new ledger
    n = keys.length(keys.BY_NAME["database"])
    print(f"   Database key in the Keychain: {n or 0} characters (never shown).")
    return n == 64


def step_bank(ctx: Ctx) -> bool:
    if keychain.get_text(keychain.SIMPLEFIN_ACCESS_SERVICE) is not None:
        print("   Lunch Flow is already connected.")
        return True
    if not _yes(ctx.ask, "   Connect your banks through Lunch Flow now?"):
        return False

    def read(prompt: str) -> str:
        return keys.read_secret(prompt, ctx.hidden)

    return bank.guide(current_user(), ctx.open_url, ctx.ask, ctx.token, read) == 0


def step_models(ctx: Ctx) -> bool:
    if not _yes(ctx.ask, "   Set up the local AI models now?"):
        return False
    return models.install(None, ask=ctx.ask) == 0


def step_cloud(ctx: Ctx) -> bool:
    print(
        "   Optional: a Gemini key lets you send redacted questions to the cloud, "
        "one conversation at a time. Paid-tier keys only; spend is capped at £9."
    )
    if keychain.gemini_key() or not _yes(ctx.ask, "   Add a Gemini key now?", False):
        return True
    return keys.set_key("gemini", ctx.hidden) == 0


def step_jobs(ctx: Ctx) -> bool:
    print(f"   Daily sync and backup: {schedule.install(ctx.agents_dir, ctx.ctl)}")
    if _yes(ctx.ask, "   Register an external backup drive now?", False):
        backup.setup(ctx.agents_dir, ctx.ctl)
    print(
        "   The morning briefing is built by the app when you open it; no job needed."
    )
    return True


def step_finish(ctx: Ctx) -> bool:
    doctor.run()
    if _yes(ctx.ask, "   Open the app now?"):
        launcher.default().start()
    return True


STEPS: tuple[tuple[str, str, Callable[[Ctx], bool]], ...] = (
    ("key", "Database key", step_key),
    ("bank", "Bank (Lunch Flow)", step_bank),
    ("models", "Local AI", step_models),
    ("cloud", "Optional cloud", step_cloud),
    ("jobs", "Background jobs", step_jobs),
    ("finish", "Finish", step_finish),
)


def run(
    ctx: Ctx | None = None,
    restart: bool = False,
    steps: tuple[tuple[str, str, Callable[[Ctx], bool]], ...] = STEPS,
) -> int:
    ctx = ctx or Ctx()
    if restart:
        _state_path().unlink(missing_ok=True)
    done = done_steps()
    left = 0
    for i, (name, title, fn) in enumerate(steps, 1):
        if name in done:
            print(f"{i}. {title}: done earlier")
            continue
        print(f"{i}. {title}")
        try:
            ok = fn(ctx)
        except (OSError, ValueError, LookupError) as e:
            print(f"   fa: {e}", file=sys.stderr)  # messages never carry a secret
            ok = False
        if ok:
            _mark(name)
        else:
            left += 1
            print("   Not finished; `fa setup` picks it up next time.")
    return 1 if left else 0


def read_token_stdin(stream: TextIO | None = None) -> str:
    value = (stream or sys.stdin).read().strip()
    if not value:
        raise keys.EmptySecret("no setup token on stdin")
    return value


# --- uninstall ----------------------------------------------------------------

PURGE_WORD = "delete my data"


def uninstall(
    purge: bool,
    ask: Ask = input,
    run_cmd: Callable[[list[str]], int] | None = None,
    agents_dir: Path | None = None,
    ctl: watch.Launchctl | None = None,
) -> int:
    for label in (schedule.LABEL, backup.LABEL, watch.LABEL):
        watch.remove_agent(label, agents_dir, ctl)
    print("Removed the background jobs.")
    if purge:
        data = default_db_path().parent
        print(
            f"--purge deletes the ledger in {data} and the app's Keychain items. "
            "Without a backup and its recovery key this cannot be undone."
        )
        if ask(f"Type '{PURGE_WORD}' to continue: ").strip() != PURGE_WORD:
            print("Data kept.")
            return 1
        # only ever the app's own folder; a custom FA_DB_PATH loses just its file
        if data.name == DEFAULT_DB.parent.name and data.is_dir():
            shutil.rmtree(data)
        else:
            default_db_path().unlink(missing_ok=True)
        for item in keys.ITEMS:
            keychain.delete_item(item.service, item.account)
        print("Data and keys deleted.")
    else:
        print(f"Your data stays in {default_db_path().parent}.")
    uv = shutil.which("uv")
    run_cmd = run_cmd or (
        lambda a: subprocess.run(a, check=False).returncode  # noqa: S603
    )
    if uv and run_cmd([uv, "tool", "uninstall", "finance-agent"]) == 0:
        print("Removed the app. Goodbye.")
    else:
        print("Remove the app with: uv tool uninstall finance-agent")
    return 0
