"""`fa sync --install`: a daily launchd agent that runs `fa sync --notify`."""

from pathlib import Path

from finance_agent.cli import watch

LABEL = "com.finance-agent.sync"
HOUR, MINUTE = 7, 30  # launchd runs a missed slot when the Mac wakes


def install(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> Path:
    return watch.write_agent(
        LABEL,
        ["sync", "--notify"],
        {"StartCalendarInterval": {"Hour": HOUR, "Minute": MINUTE}},
        agents_dir,
        ctl,
    )


def remove(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> None:
    watch.remove_agent(LABEL, agents_dir, ctl)
