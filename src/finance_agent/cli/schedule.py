"""`fa sync --install`: a launchd agent every few hours that runs `fa sync --notify`."""

from pathlib import Path

from finance_agent.cli import watch

LABEL = "com.finance-agent.sync"
# SimpleFIN is pull-only and the aggregator refreshes about 4 times a day, so
# polling faster adds nothing. RunAtLoad: one run at login; StartInterval fires
# after a wake; an overlapping run skips on the sync lock.
INTERVAL = 6 * 60 * 60


def install(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> Path:
    return watch.write_agent(
        LABEL,
        ["sync", "--notify"],
        {"StartInterval": INTERVAL, "RunAtLoad": True},
        agents_dir,
        ctl,
    )


def remove(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> None:
    watch.remove_agent(LABEL, agents_dir, ctl)
