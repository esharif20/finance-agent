"""`fa models` (aliases `fa brain`, `fa ai`): the machine, the supported local
models with the project's own benchmark results, and one recommendation.
`fa models install` pulls, warms and verifies through local Ollama, then
unloads (keep_alive 0); `fa models remove` frees the disk. `fa setup` reuses it.

Benchmark figures come from `evals/results/*.json`, never from here. Ollama is
reached on localhost only; tests pass a fake `Ollama` and `Machine`."""

import json
import os
import re
import shutil
import subprocess
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

from finance_agent.assistant.ollama import DEFAULT_CHOICE, MODELS
from finance_agent.categorise import embedding
from finance_agent.identity import current_user
from finance_agent.ledger.settings import Choice, Settings
from finance_agent.storage import open_db

OLLAMA = "http://localhost:11434"
EMBED_MODEL = embedding.MODEL
RESULTS_ENV = "FA_EVALS_DIR"
RESULTS = Path(__file__).resolve().parents[3] / "evals" / "results"
HEADROOM_GB = Decimal("1.5")  # the OS, the browser and the app itself
GB = Decimal(1024**3)
# shortcut: used only when evals/results has no peak for a model (the tool
# install ships no results); figures from the #144 memory gate notes in
# assistant/ollama.py and categorise/llm.py. Shown marked "est.".
ESTIMATED_PEAK_GB = {
    MODELS["fast"]: Decimal("4.5"),
    MODELS["careful"]: Decimal("6.8"),
    EMBED_MODEL: Decimal("0.6"),
}
NAMES: dict[str, str] = {"4b": MODELS["fast"], "9b": MODELS["careful"]}
CHOICE_OF: dict[str, Choice] = {MODELS["fast"]: "fast", MODELS["careful"]: "careful"}
CHOICE_NAME: dict[str, str] = {c: n for n, c in CHOICE_OF.items()}
METRICS = ("tool_call_accuracy", "grounding_pass_rate", "latency_p50_ms")


@dataclass(frozen=True)
class Machine:
    chip: str
    total_gb: Decimal
    free_gb: Decimal
    disk_free_gb: Decimal


@dataclass(frozen=True)
class Bench:
    metrics: dict[str, Decimal] = field(default_factory=lambda: {})
    peak_gb: Decimal | None = None  # measured; None when no result has one


@dataclass(frozen=True)
class Recommendation:
    model: str | None  # None: no chat model fits
    reason: str


class Ollama(Protocol):
    def installed(self) -> set[str] | None: ...  # None: Ollama not running
    def pull(self, name: str) -> bool: ...
    def remove(self, name: str) -> bool: ...
    def verify(self, name: str, embed: bool) -> bool: ...


def _gb(n: int) -> Decimal:
    return (Decimal(n) / GB).quantize(Decimal("0.1"))


def _sysctl(key: str) -> str:
    r = subprocess.run(  # noqa: S603 - fixed argv
        ["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True, check=False
    )
    return r.stdout.strip()


def read_machine(home: Path | None = None) -> Machine:
    """This Mac: chip, total and free memory (free + inactive + speculative
    pages, what macOS can hand out without swapping) and free disk."""
    total = int(_sysctl("hw.memsize") or 0)
    vm = subprocess.run(  # noqa: S603 - fixed argv
        ["/usr/bin/vm_stat"], capture_output=True, text=True, check=False
    ).stdout
    page = int(m.group(1)) if (m := re.search(r"page size of (\d+)", vm)) else 16384
    pages = sum(
        int(m.group(1))
        for k in ("free", "inactive", "speculative")
        if (m := re.search(rf"Pages {k}:\s+(\d+)", vm))
    )
    disk = shutil.disk_usage(home or Path.home()).free
    return Machine(
        _sysctl("machdep.cpu.brand_string") or "unknown chip",
        _gb(total),
        _gb(pages * page),
        _gb(disk),
    )


def _dict(v: object) -> dict[str, Any]:
    return cast(dict[str, Any], v) if isinstance(v, dict) else {}


def benchmarks(folder: Path | None = None) -> dict[str, Bench]:
    """Per model: the latest metrics found in the results files, and the
    highest measured peak memory."""
    env = os.environ.get(RESULTS_ENV)
    folder = folder or (Path(env) if env else RESULTS)
    out: dict[str, Bench] = {}
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            doc = json.loads(path.read_text(), parse_float=Decimal)
        except (OSError, ValueError):
            continue
        for run in map(_dict, _dict(_dict(doc).get("models")).values()):
            name, metrics = run.get("model"), _dict(run.get("metrics"))
            if not isinstance(name, str) or name not in ESTIMATED_PEAK_GB:
                continue
            prev = out.get(name, Bench())
            got = {
                k: Decimal(v)
                for k, v in metrics.items()
                if k in METRICS and isinstance(v, (int, Decimal))
            }
            peak = metrics.get("peak_memory_gb")
            peaks = [p for p in (prev.peak_gb, peak) if isinstance(p, (int, Decimal))]
            out[name] = Bench(
                {**prev.metrics, **got}, max(map(Decimal, peaks)) if peaks else None
            )
    return out


def peak(name: str, bench: Mapping[str, Bench]) -> Decimal:
    b = bench.get(name)
    return b.peak_gb if b and b.peak_gb is not None else ESTIMATED_PEAK_GB[name]


def recommend(m: Machine, bench: Mapping[str, Bench]) -> Recommendation:
    """The biggest chat model whose peak, plus the embedder and headroom, fits
    in memory free now."""
    need = {
        k: peak(NAMES[k], bench) + peak(EMBED_MODEL, bench) + HEADROOM_GB for k in NAMES
    }
    head = f"{m.total_gb:.0f} GB, about {m.free_gb:.0f} GB free"
    if m.free_gb >= need["9b"]:
        return Recommendation(
            NAMES["9b"], f"{head}: 9b fits with room to spare; 4b if you want speed."
        )
    if m.free_gb >= need["4b"]:
        return Recommendation(
            NAMES["4b"],
            f"{head}: 4b for everyday, 9b for careful only after closing apps; "
            f"9b needs about {need['9b']:.1f} GB and would swap now.",
        )
    return Recommendation(
        None,
        f"{head}: even 4b needs about {need['4b']:.1f} GB. Close some apps and run "
        "`fa models` again; rules and your corrections still work meanwhile.",
    )


# --- Ollama on localhost --------------------------------------------------------


def ollama_json(
    path: str, body: dict[str, Any] | None = None, timeout: int = 2
) -> dict[str, Any] | None:
    """GET (or POST `body`) to local Ollama; None when it does not answer."""
    req = urllib.request.Request(  # noqa: S310 - fixed localhost URL
        OLLAMA + path,
        None if body is None else json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            doc: object = json.load(r)
    except (OSError, ValueError):
        return None
    return _dict(doc)


def model_names(doc: dict[str, Any]) -> list[str]:
    models: list[Any] = doc.get("models") or []
    return [str(_dict(m).get("name")) for m in models]


class LocalOllama:
    """The real one: `ollama` for pull and rm (its own progress bar), HTTP on
    localhost for listing and the warm-up check."""

    def installed(self) -> set[str] | None:
        doc = ollama_json("/api/tags")
        return None if doc is None else set(model_names(doc))

    def _cli(self, *args: str) -> bool:
        exe = shutil.which("ollama")
        if exe is None:
            return False
        return subprocess.run([exe, *args], check=False).returncode == 0  # noqa: S603

    def pull(self, name: str) -> bool:
        return self._cli("pull", name)

    def remove(self, name: str) -> bool:
        return self._cli("rm", name)

    def verify(self, name: str, embed: bool) -> bool:
        """Load once on a tiny synthetic prompt, then unload (keep_alive 0)."""
        if embed:
            doc = ollama_json(
                "/api/embed",
                {"model": name, "input": ["synthetic coffee shop"], "keep_alive": 0},
                120,
            )
            return bool(doc and doc.get("embeddings"))
        doc = ollama_json(
            "/api/generate",
            {
                "model": name,
                "prompt": "Reply with the single word: ready",
                "stream": False,
                "think": False,
                "keep_alive": 0,
                "options": {"num_predict": 8},
            },
            300,
        )
        return bool(doc and str(doc.get("response", "")).strip())


# --- commands ---------------------------------------------------------------------


def choice() -> Choice | None:
    try:
        engine = open_db(create=False)
    except (OSError, ValueError, LookupError):
        return None
    try:
        with engine.begin() as conn:
            return Settings(conn, current_user()).assistant_model()
    finally:
        engine.dispose()


def _set_choice(choice: Choice) -> bool:
    try:
        engine = open_db(create=False)
    except (OSError, ValueError, LookupError):
        return False
    try:
        with engine.begin() as conn:
            Settings(conn, current_user()).set_assistant_model(choice)
        return True
    finally:
        engine.dispose()


def _fmt(b: Bench | None, key: str) -> str:
    v = b.metrics.get(key) if b else None
    if v is None:
        return "-"
    return f"{v / 1000:.1f}s" if key.endswith("_ms") else f"{v:.2f}"


def show(
    ollama: Ollama | None = None,
    machine: Callable[[], Machine] = read_machine,
    bench: Mapping[str, Bench] | None = None,
    current: Callable[[], Choice | None] = choice,
) -> int:
    ollama = ollama or LocalOllama()
    m = machine()
    bench = benchmarks() if bench is None else bench
    have = ollama.installed()
    used = MODELS[current() or DEFAULT_CHOICE]
    print(
        f"{m.chip} · {m.total_gb:.0f} GB memory, {m.free_gb:.1f} GB free · "
        f"{m.disk_free_gb:.0f} GB disk free"
    )
    print()
    print(f"  {'model':<22}{'tools':>7}{'ground':>8}{'p50':>7}{'peak':>11}  state")
    for name in (*NAMES.values(), EMBED_MODEL):
        b = bench.get(name)
        pk = (
            f"{b.peak_gb:.1f} GB"
            if b and b.peak_gb is not None
            else (f"{ESTIMATED_PEAK_GB[name]:.1f} est.")
        )
        state = [] if have is None else ["installed" if name in have else "not pulled"]
        if name == used:
            state.append("Settings choice")
        print(
            f"  {name:<22}{_fmt(b, METRICS[0]):>7}{_fmt(b, METRICS[1]):>8}"
            f"{_fmt(b, METRICS[2]):>7}{pk:>11}  {', '.join(state)}"
        )
    if have is None:
        print("\nOllama is not running; `fa models install` sets it up.")
    r = recommend(m, bench)
    print(f"\nRecommended: {r.model or 'none for now'}. {r.reason}")
    print("Figures: this project's benchmarks in evals/results; '-' not measured.")
    return 0


def _ensure_ollama(ask: Callable[[str], str]) -> bool:
    if shutil.which("ollama"):
        return True
    print("Ollama runs the local models and is not installed.")
    if not shutil.which("brew"):
        print("Install it from ollama.com, start it, then run this again.")
        return False
    if ask("Install it with `brew install ollama` now? [y/N] ").strip().lower() != "y":
        return False
    subprocess.run(["brew", "install", "ollama"], check=False)  # noqa: S603, S607
    subprocess.run(["brew", "services", "start", "ollama"], check=False)  # noqa: S603, S607
    return shutil.which("ollama") is not None


def install(
    target: str | None,
    ollama: Ollama | None = None,
    machine: Callable[[], Machine] = read_machine,
    bench: Mapping[str, Bench] | None = None,
    ask: Callable[[str], str] = input,
    set_choice: Callable[[Choice], bool] = _set_choice,
    need_binary: Callable[[Callable[[str], str]], bool] = _ensure_ollama,
) -> int:
    """Pull the chat model (`target`: 4b, 9b, a full name, or None for the
    recommendation) and the embedder, verify each once, set Settings."""
    if target is None:
        r = recommend(machine(), benchmarks() if bench is None else bench)
        print(r.reason)
        if r.model is None:
            return 1
        target = r.model
    name = NAMES.get(target, target)
    if name not in CHOICE_OF:
        print(f"fa: unknown model; choose one of {', '.join(NAMES)}")
        return 2
    if not need_binary(ask):
        return 1
    ollama = ollama or LocalOllama()
    if ollama.installed() is None:
        print("fa: Ollama is installed but not running; start it (`ollama serve`).")
        return 1
    for m, embed in ((name, False), (EMBED_MODEL, True)):
        print(f"Pulling {m}…")
        if not ollama.pull(m):
            print(f"fa: could not pull {m}")
            return 1
        if not ollama.verify(m, embed):
            print(f"fa: {m} did not answer the check prompt")
            return 1
        print(f"  {m} ready (unloaded again)")
    if set_choice(CHOICE_OF[name]):
        print(f"Assistant model set to {name}.")
    else:
        print("No ledger yet: choose the model in Settings after `fa setup`.")
    return 0


def remove(target: str, ollama: Ollama | None = None) -> int:
    name = NAMES.get(target, target)
    if name not in ESTIMATED_PEAK_GB:
        print("fa: unknown model")
        return 2
    ok = (ollama or LocalOllama()).remove(name)
    print(f"Removed {name}" if ok else f"fa: could not remove {name}")
    return 0 if ok else 1
