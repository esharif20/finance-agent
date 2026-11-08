"""macOS notifications, shared by the CLI and the API (#108). A body carries
counts, a Merchant's name or a date at most: never an amount or account field."""

import subprocess
from collections.abc import Callable

TITLE = "Finance Agent"

Notifier = Callable[[str, str], None]  # (title, body)

_SCRIPT = (
    "on run argv\n"
    "display notification (item 2 of argv) with title (item 1 of argv)\n"
    "end run"
)


def printed(_title: str, body: str) -> None:
    """For a command the user runs by hand: the alert goes to the terminal."""
    print(f"Alert: {body}")


def osascript(title: str, body: str) -> None:
    # argv, never interpolated into the script, so a name cannot inject code
    subprocess.run(  # noqa: S603
        ["/usr/bin/osascript", "-e", _SCRIPT, title, body], check=False
    )
