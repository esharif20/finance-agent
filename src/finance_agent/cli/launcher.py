"""Start, find and stop the local API server (`fa`, `fa stop`)."""

import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path

from finance_agent.api import DEFAULT_PORT, HOST
from finance_agent.storage import default_db_path


class ServerDidNotStart(RuntimeError):
    pass


def _probe(port: int) -> bool:
    """True only if *our* server answers, not just any process on the port."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/health", timeout=1) as r:
            return json.load(r).get("app") == "finance-agent"
    except (OSError, ValueError):
        return False


def _spawn(port: int) -> int:
    p = subprocess.Popen(  # noqa: S603 - fixed argv, our own module
        [sys.executable, "-m", "finance_agent.api"],
        env={**os.environ, "FA_PORT": str(port)},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return p.pid


def _kill(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def _open_browser(url: str) -> None:
    if not os.environ.get("FA_NO_BROWSER"):  # set by the smoke test
        webbrowser.open(url)


class Launcher:
    def __init__(
        self,
        pid_file: Path,
        port: int,
        probe: Callable[[int], bool] = _probe,
        spawn: Callable[[int], int] = _spawn,
        kill: Callable[[int], None] = _kill,
        open_url: Callable[[str], object] = _open_browser,
        wait: int = 15,
    ) -> None:
        self.pid_file, self.port, self.wait = pid_file, port, wait
        self._probe, self._spawn, self._kill, self._open = probe, spawn, kill, open_url

    def start(self) -> None:
        """Start the server unless ours already answers, then open the browser."""
        if not self._probe(self.port):
            pid = self._spawn(self.port)
            self.pid_file.parent.mkdir(parents=True, exist_ok=True)
            self.pid_file.write_text(f"{pid} {self.port}")
            deadline = time.monotonic() + self.wait
            while not self._probe(self.port):
                if time.monotonic() >= deadline:
                    raise ServerDidNotStart("the server did not answer in time")
                time.sleep(0.2)
        self._open(f"http://{HOST}:{self.port}/")

    def stop(self) -> bool:
        """Stop the recorded server. Returns False if none was running."""
        try:
            pid, port = (int(x) for x in self.pid_file.read_text().split())
        except (OSError, ValueError):
            return False
        # a stale pid may belong to another process now: kill only if ours answers
        running = self._probe(port)
        if running:
            self._kill(pid)
        self.pid_file.unlink(missing_ok=True)
        return running


def default() -> Launcher:
    return Launcher(
        default_db_path().parent / "server.pid",
        int(os.environ.get("FA_PORT", DEFAULT_PORT)),
        open_url=_open_browser,
    )
