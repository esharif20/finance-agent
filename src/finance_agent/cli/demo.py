"""`fa demo`: the #296 synthetic demo on this Mac, with no setup. Persona
ledgers are built in a temp FA_DEMO_DIR, the demo process (FA_DEMO=1) serves
them on a free localhost port, and Ctrl-C stops it and deletes the temp data.
It never opens the real data directory or the Keychain: the child process runs
under demo.guard, and this side only spawns and waits."""

import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from finance_agent.cli import banner, launcher
from finance_agent.identity import HOST

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "scripts" / "build_demo_db.py"
# TODO(#299): serve the demo build once it lands; until then the normal web build
WEB = REPO / "web" / "dist"
PERSONAS = 6

Spawn = Callable[[list[str], Mapping[str, str]], "subprocess.Popen[bytes]"]


def _spawn(argv: list[str], env: Mapping[str, str]) -> "subprocess.Popen[bytes]":
    return subprocess.Popen(argv, env=dict(env), stdin=subprocess.DEVNULL)  # noqa: S603


def free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return int(s.getsockname()[1])


def demo_env(root: Path, port: int) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FA_")}
    return {
        **env,
        "FA_DEMO": "1",
        "FA_DEMO_DIR": str(root),
        "FA_DEMO_SECRET": secrets.token_hex(32),
        "FA_PORT": str(port),
    }


def list_personas() -> int:
    for n in range(PERSONAS):
        print(f"  {n + 1}  a fictional UK household, synthetic history #{n}")
    print("Run `fa demo --persona N` to open one; without it, each visit gets one.")
    return 0


def run(
    persona: int | None = None,
    spawn: Spawn = _spawn,
    open_url: Callable[[str], object] = launcher._open_browser,  # pyright: ignore[reportPrivateUsage]
    probe: Callable[[int], bool] = launcher._probe,  # pyright: ignore[reportPrivateUsage]
    wait: float = 30,
) -> int:
    if persona is not None and not 1 <= persona <= PERSONAS:
        print(f"fa: persona must be 1 to {PERSONAS}; `fa demo --list` shows them")
        return 2
    if not BUILD.is_file():
        print("fa: `fa demo` runs from a clone of the repository (scripts/ is missing)")
        return 1
    root = Path(tempfile.mkdtemp(prefix="fa-demo-")).resolve()
    port = free_port()
    env = demo_env(root, port)
    server: subprocess.Popen[bytes] | None = None
    try:
        print("Building the synthetic personas…")
        if spawn([sys.executable, str(BUILD)], env).wait() != 0:
            print("fa: could not build the demo personas")
            return 1
        if persona is not None:  # keep only the one asked for: every visit gets it
            people = root / "personas"
            chosen = people / f"{persona - 1}.db"
            for p in people.glob("*.db"):
                if p != chosen:
                    p.unlink()
            chosen.rename(people / "0.db")
        if WEB.is_dir():
            shutil.copytree(WEB, root / "web")
        server = spawn(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "finance_agent.demo.app:app",
                "--host",
                HOST,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            env,
        )
        deadline = time.monotonic() + wait
        while not probe(port):
            if server.poll() is not None or time.monotonic() >= deadline:
                print("fa: the demo did not start")
                return 1
            time.sleep(0.2)
        who = f"persona {persona}" if persona else "a random persona"
        banner.show(f"demo · synthetic data · {who} · http://{HOST}:{port}/")
        print("Synthetic data only. Ctrl-C stops the demo and deletes it.")
        open_url(f"http://{HOST}:{port}/")
        server.wait()
        return 0
    except KeyboardInterrupt:
        print("\nStopping the demo.")
        return 0
    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
        shutil.rmtree(root, ignore_errors=True)
