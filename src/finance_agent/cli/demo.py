"""`fa demo`: the #296 synthetic demo on this Mac, with no setup. Persona
ledgers are built in a temp FA_DEMO_DIR, the demo process (FA_DEMO=1) serves
them and `web/dist-demo` (landing page at `/`, app at `/app`, as vercel.json
routes it) on a free localhost port, and Ctrl-C stops it and deletes the temp
data. Without a demo build it builds one (pnpm) or points at the hosted demo.
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
WEB_SRC = REPO / "web"
WEB = WEB_SRC / "dist-demo"
HOSTED = "https://finance-agent-demo-chi.vercel.app"
PERSONAS = 6

Spawn = Callable[[list[str], Mapping[str, str]], "subprocess.Popen[bytes]"]


def _spawn(argv: list[str], env: Mapping[str, str]) -> "subprocess.Popen[bytes]":
    return subprocess.Popen(  # noqa: S603
        argv, env=dict(env), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL
    )


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


def _hosted(open_browser: bool, open_url: Callable[[str], object], why: str) -> int:
    print(f"{why} Using the hosted demo instead: {HOSTED}")
    if open_browser:
        print("Opening the landing page…")
        open_url(HOSTED)
    return 0


def _ensure_web(spawn: Spawn, which: Callable[[str], str | None]) -> bool:
    """True once `web/dist-demo` exists, building it once from a source checkout."""
    if WEB.is_dir():
        return True
    if not (WEB_SRC / "package.json").is_file() or which("pnpm") is None:
        return False
    env = dict(os.environ)
    web = str(WEB_SRC)
    print("Building the demo site (first run only, about a minute)…")
    if (
        not (WEB_SRC / "node_modules").is_dir()
        and spawn(["pnpm", "--dir", web, "install", "--frozen-lockfile"], env).wait()
    ):
        return False
    return spawn(["pnpm", "--dir", web, "run", "build:demo"], env).wait() == 0 and (
        WEB.is_dir()
    )


def run(
    persona: int | None = None,
    spawn: Spawn = _spawn,
    open_url: Callable[[str], object] = launcher._open_browser,  # pyright: ignore[reportPrivateUsage]
    probe: Callable[[int], bool] = launcher._probe,  # pyright: ignore[reportPrivateUsage]
    wait: float = 30,
    open_browser: bool = True,
    which: Callable[[str], str | None] = shutil.which,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if persona is not None and not 1 <= persona <= PERSONAS:
        print(f"fa: persona must be 1 to {PERSONAS}; `fa demo --list` shows them")
        return 2
    banner.show()
    if not BUILD.is_file() or not _ensure_web(spawn, which):
        return _hosted(open_browser, open_url, "The local demo can't be built here.")
    root = Path(tempfile.mkdtemp(prefix="fa-demo-")).resolve()
    port = free_port()
    env = demo_env(root, port)
    server: subprocess.Popen[bytes] | None = None
    try:
        print(f"Building {PERSONAS} synthetic Personas…")
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
        url = f"http://{HOST}:{port}/"
        print(f"Serving on {url.rstrip('/')}")
        deadline = time.monotonic() + wait
        while not probe(port):
            if server.poll() is not None or time.monotonic() >= deadline:
                print("fa: the demo did not start")
                return 1
            sleep(0.2)
        print("Synthetic data only. Ctrl-C stops the demo and deletes it.")
        if open_browser:
            print("Opening the landing page…")
            open_url(url)
        else:
            print(f"Landing page: {url}")
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
