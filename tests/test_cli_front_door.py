"""#311: the banner, `fa status`/`month`/`subs`, `fa doctor`, `fa models`,
`fa demo` and install.sh. Fakes only: no Ollama, launchd, network or real data."""

import io
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from finance_agent.cli import banner, demo, doctor, main, models
from finance_agent.identity import current_user
from finance_agent.storage import DEFAULT_DB
from synthetic_lloyds import generate_histories, render_export

ROOT = Path(__file__).resolve().parents[1]


def _const(v: object) -> Callable[..., Any]:
    return lambda *_a, **_k: v


@pytest.fixture(autouse=True)
def _no_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every localhost Ollama call in the CLI goes through ollama_json."""
    monkeypatch.setattr(models, "ollama_json", _const(None))


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


# --- banner --------------------------------------------------------------------


def test_the_banner_is_skipped_without_a_tty() -> None:
    out = io.StringIO()
    slept: list[float] = []
    banner.show("status", out, {}, slept.append)
    assert out.getvalue() == "" and slept == []


@pytest.mark.parametrize(
    "env", [{"NO_COLOR": "1"}, {"FA_NO_ANIMATION": "1"}, {"CI": "true"}]
)
def test_the_banner_is_static_under_no_color_no_animation_or_ci(
    env: dict[str, str],
) -> None:
    out, slept = Tty(), list[float]()
    banner.show("synced 1h ago", out, env, slept.append)
    assert slept == [] and "synced 1h ago" in out.getvalue()
    if "NO_COLOR" in env:
        assert "\x1b" not in out.getvalue()


def test_the_banner_is_static_on_a_narrow_terminal() -> None:
    assert banner.mode(Tty(), {}, banner.MIN_COLS - 1) == "static"
    assert banner.mode(Tty(), {}, banner.MIN_COLS) == "animate"


def test_the_animation_lasts_about_1_2_seconds_and_restores_the_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "get_terminal_size", _const(os.terminal_size((80, 24))))
    out, slept = Tty(), list[float]()
    banner.show(None, out, {"COLORTERM": "truecolor"}, slept.append)
    assert Decimal(str(sum(slept))).quantize(Decimal("0.01")) <= Decimal("1.2")
    assert len(slept) >= 6  # at least a full face-edge-back turn of frames
    text = out.getvalue()
    assert text.count("\x1b[?25l") == 1 and text.rstrip().find("\x1b[?25h") > 0
    assert "38;2;" in text  # truecolor


def test_256_colour_fallback_without_truecolor() -> None:
    lines = banner.render(banner.final(), colour=True, truecolor=False)
    assert any("38;5;" in ln for ln in lines) and not any("38;2;" in ln for ln in lines)


def test_the_settled_coin_shows_the_pound_and_the_whole_wordmark() -> None:
    c = banner.final()
    assert any(px == banner.GOLD_DEEP for row in c for px in row)  # the £
    word = sum(1 for row in c for x, px in enumerate(row) if px and x >= banner.WORD_X)
    assert word == sum(r.count("#") for line in banner.LINES for r in line)


def test_the_coin_turns_through_face_edge_and_back() -> None:
    widths = [
        sum(1 for row in banner.frame(i)[: 2 * banner.R] for px in row if px)
        for i in range(banner.FRAMES)
    ]
    assert min(widths) < max(widths) // 3  # nearly edge-on somewhere


def test_the_readme_svg_is_synthetic_and_in_the_repo() -> None:
    svg = (ROOT / "docs" / "banner.svg").read_text()
    assert svg.startswith("<svg") and "<set " in svg and banner.TAGLINE in svg


# --- status, month, subs on a synthetic ledger -----------------------------------


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("FA_DB_PATH", str(tmp_path / "ledger.db"))
    h, _ = generate_histories(seed=3, days=60)
    csv = tmp_path / "lloyds.csv"
    csv.write_text(render_export(h, 0, 120).text)
    assert main(["import", str(csv)]) == 0
    return tmp_path


def test_month_prints_categories_with_bars(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    assert main(["month", "2026-02"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("2026-02") and "█" in out and "£" in out


def test_month_rejects_a_bad_month(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["month", "2026-13"]) == 2
    assert "YYYY-MM" in capsys.readouterr().err


def test_status_and_subs_print_without_ollama(
    ledger: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    capsys.readouterr()
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    for label in ("Last sync", "To review", "AI tools", "Cloud", "Ollama off"):
        assert label in out
    assert main(["subs"]) == 0
    assert "of £100 cap" in capsys.readouterr().out


def test_bars_are_proportional_in_eighths() -> None:
    from finance_agent.cli.status import bar

    assert bar(Decimal(50), Decimal(100), 8) == "████"
    assert bar(Decimal(1), Decimal(16), 2) == "▏"
    assert bar(Decimal(0), Decimal(10)) == "" and bar(Decimal(5), Decimal(0)) == ""


# --- doctor ----------------------------------------------------------------------


def test_doctor_reports_every_failure_with_a_fix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from finance_agent.storage import keychain

    keychain.delete_item(keychain.DB_SERVICE)
    probes = doctor.Probes(
        installed=lambda: None,
        loaded=lambda _label: False,
        agents_dir=tmp_path / "agents",
        disk_free=lambda _p: 0,
        choice=lambda: None,
    )
    assert doctor.run(probes) == 1
    out = capsys.readouterr().out
    for what in (
        "database key",
        "ledger encrypted",
        "Ollama reachable",
        "daily sync agent",
        "backup agent",
        "disk free",
        "backup drive",
    ):
        line = next(ln for ln in out.splitlines() if what in ln)
        assert line.lstrip().startswith("✗") and "→" in line


def test_doctor_names_missing_models_when_ollama_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    probes = doctor.Probes(
        installed=lambda: {models.EMBED_MODEL},
        loaded=lambda _label: True,
        agents_dir=tmp_path,
        disk_free=lambda _p: 10**12,
        choice=lambda: "fast",
    )
    results = doctor.checks(probes)
    assert (
        True,
        f"model {models.EMBED_MODEL} present",
        "`fa models install`",
    ) in results
    assert (
        False,
        f"model {models.NAMES['4b']} present",
        "`fa models install`",
    ) in results


# --- models ----------------------------------------------------------------------


def _machine(free: str, total: str = "16") -> models.Machine:
    return models.Machine("Apple M1 Pro", Decimal(total), Decimal(free), Decimal(100))


def _results(folder: Path, peaks: Mapping[str, str]) -> Path:
    folder.mkdir()
    for i, (name, peak) in enumerate(peaks.items()):
        (folder / f"run{i}.json").write_text(
            json.dumps(
                {
                    "models": {
                        name: {
                            "model": name,
                            "metrics": {
                                "tool_call_accuracy": 0.8,
                                "grounding_pass_rate": 0.7,
                                "latency_p50_ms": 4000,
                                "peak_memory_gb": float(peak),
                            },
                        }
                    }
                }
            )
        )
    return folder


@pytest.mark.parametrize(
    ("free", "peak9", "peak4", "pick"),
    [
        ("12", "6.8", "4.5", "9b"),
        ("8.9", "6.8", "4.5", "9b"),  # 6.8 + 0.6 embedder + 1.5 headroom
        ("8.8", "6.8", "4.5", "4b"),
        ("6.6", "6.8", "4.5", "4b"),  # 4.5 + 0.6 + 1.5
        ("6.5", "6.8", "4.5", None),
        ("12", "11", "4.5", "4b"),  # a heavier measured 9b changes the answer
        ("3", "6.8", "1.0", None),
        ("3.1", "6.8", "1.0", "4b"),  # a lighter measured 4b fits
    ],
)
def test_recommendation_uses_measured_peaks_plus_headroom(
    tmp_path: Path, free: str, peak9: str, peak4: str, pick: str | None
) -> None:
    bench = models.benchmarks(
        _results(
            tmp_path / "results",
            {
                models.NAMES["9b"]: peak9,
                models.NAMES["4b"]: peak4,
                models.EMBED_MODEL: "0.6",
            },
        )
    )
    r = models.recommend(_machine(free), bench)
    assert r.model == (models.NAMES[pick] if pick else None)
    assert f"about {Decimal(free):.0f} GB free" in r.reason


def test_benchmarks_come_from_the_results_files(tmp_path: Path) -> None:
    bench = models.benchmarks(_results(tmp_path / "r", {models.NAMES["9b"]: "6.75"}))
    b = bench[models.NAMES["9b"]]
    assert b.peak_gb == Decimal("6.75")
    assert b.metrics["tool_call_accuracy"] == Decimal("0.8")
    assert models.NAMES["4b"] not in bench  # never invented


def test_the_repos_own_results_are_read() -> None:
    bench = models.benchmarks()
    assert bench[models.NAMES["9b"]].peak_gb is not None


class FakeOllama:
    def __init__(self, have: set[str] | None = None, verify_ok: bool = True) -> None:
        self.have, self.ok = have, verify_ok
        self.pulled: list[str] = []
        self.verified: list[tuple[str, bool]] = []
        self.removed: list[str] = []

    def installed(self) -> set[str] | None:
        return self.have

    def pull(self, name: str) -> bool:
        self.pulled.append(name)
        return True

    def remove(self, name: str) -> bool:
        self.removed.append(name)
        return True

    def verify(self, name: str, embed: bool) -> bool:
        self.verified.append((name, embed))
        return self.ok


def test_install_pulls_verifies_and_selects_the_recommendation() -> None:
    fake, chosen = FakeOllama(set()), list[str]()
    rc = models.install(
        None,
        fake,
        machine=lambda: _machine("7"),
        bench={},
        set_choice=lambda c: chosen.append(c) or True,
        need_binary=lambda _ask: True,
    )
    assert rc == 0
    assert fake.pulled == [models.NAMES["4b"], models.EMBED_MODEL]
    assert fake.verified == [(models.NAMES["4b"], False), (models.EMBED_MODEL, True)]
    assert chosen == ["fast"]


def test_install_stops_when_a_model_fails_its_check() -> None:
    fake = FakeOllama(set(), verify_ok=False)
    rc = models.install(
        "9b", fake, set_choice=lambda _c: True, need_binary=lambda _ask: True
    )
    assert rc == 1 and fake.pulled == [models.NAMES["9b"]]


def test_install_refuses_when_ollama_is_not_running_or_unknown_model() -> None:
    assert models.install("9b", FakeOllama(None), need_binary=lambda _a: True) == 1
    assert models.install("70b", FakeOllama(set()), need_binary=lambda _a: True) == 2


def test_remove_and_show(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeOllama({models.NAMES["9b"]})
    assert models.remove("4b", fake) == 0 and fake.removed == [models.NAMES["4b"]]
    assert models.show(fake, lambda: _machine("12"), {}, lambda: "careful") == 0
    out = capsys.readouterr().out
    assert "Settings choice" in out and "est." in out and "Recommended:" in out


def test_models_aliases_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(models, "show", lambda: seen.append("show") or 0)
    assert main(["brain"]) == 0 and main(["ai"]) == 0 and seen == ["show", "show"]


# --- demo ------------------------------------------------------------------------


class FakeProc:
    def __init__(self, rc: int = 0) -> None:
        self.rc = rc

    def wait(self, timeout: float | None = None) -> int:
        return self.rc

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def test_demo_runs_in_a_temp_dir_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def spawn(argv: list[str], env: Mapping[str, str]) -> Any:
        calls.append((argv, dict(env)))
        if argv[-1].endswith("build_demo_db.py"):
            people = Path(env["FA_DEMO_DIR"]) / "personas"
            people.mkdir(parents=True)
            for n in range(demo.PERSONAS):
                (people / f"{n}.db").write_text(f"# synthetic {n}")
        return FakeProc()

    seen: list[list[str]] = []

    def opened(url: str) -> None:
        root = Path(calls[0][1]["FA_DEMO_DIR"])
        seen.append(sorted(p.read_text() for p in (root / "personas").glob("*.db")))
        assert url.startswith("http://127.0.0.1:")

    assert demo.run(3, spawn, opened, probe=lambda _p: True) == 0
    env = calls[0][1]
    root = Path(env["FA_DEMO_DIR"])
    assert env["FA_DEMO"] == "1" and len(env["FA_DEMO_SECRET"]) >= 32
    assert "FA_DB_PATH" not in env  # nothing points the child at a real ledger
    assert not root.is_relative_to(DEFAULT_DB.parent)
    assert seen == [["# synthetic 2"]]  # only the chosen persona, as 0.db
    assert not root.exists()  # deleted afterwards
    assert "finance_agent.demo.app:app" in calls[1][0]


def test_demo_cleans_up_on_ctrl_c() -> None:
    roots: list[Path] = []

    def spawn(argv: list[str], env: Mapping[str, str]) -> Any:
        roots.append(Path(env["FA_DEMO_DIR"]))
        raise KeyboardInterrupt

    assert demo.run(None, spawn, lambda _u: None, probe=lambda _p: True) == 0
    assert roots and not roots[0].exists()


def test_demo_rejects_a_bad_persona_and_lists(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert demo.run(99, _const(FakeProc())) == 2
    assert main(["demo", "--list"]) == 0
    assert "--persona" in capsys.readouterr().out


def test_demo_never_opens_the_keychain(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    def no(*_a: object) -> None:
        raise AssertionError("the demo touched the Keychain")

    monkeypatch.setattr(keyring, "get_password", no)
    monkeypatch.setattr(keyring, "set_password", no)
    assert demo.run(None, _const(FakeProc(1)), lambda _u: None) == 1


# --- install.sh ---------------------------------------------------------------


def test_install_sh_parses_and_passes_shellcheck() -> None:
    script = ROOT / "install.sh"
    assert subprocess.run(["sh", "-n", str(script)], check=False).returncode == 0  # noqa: S603, S607
    sc = shutil.which("shellcheck")
    if sc is None:
        pytest.skip("shellcheck not installed (CI's runner has it)")
    r = subprocess.run([sc, str(script)], capture_output=True, text=True, check=False)  # noqa: S603
    assert r.returncode == 0, r.stdout


def test_install_sh_needs_no_sudo_and_pins_what_it_downloads() -> None:
    text = (ROOT / "install.sh").read_text()
    assert not re.search(r"^\s*sudo\b|[|&;]\s*sudo\b", text, re.MULTILINE)
    assert "shasum -a 256" in text and "UV_INSTALLER_SHA256=" in text


def test_the_status_line_never_needs_a_network(ledger: Path) -> None:
    from finance_agent.cli import status
    from finance_agent.storage import open_db

    engine = open_db(create=False)
    try:
        now = datetime(2026, 3, 1, tzinfo=UTC)
        line = status.one_line(status.snapshot(engine, current_user(), now.date()), now)
    finally:
        engine.dispose()
    assert "accounts" in line and "to review" in line


def test_the_cli_only_dials_local_ollama() -> None:
    assert models.OLLAMA == "http://localhost:11434"
    assert "urlopen" not in (ROOT / "src/finance_agent/cli/status.py").read_text()


def test_status_reads_cloud_spend_only_from_an_existing_meter(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from finance_agent.cli import status
    from finance_agent.storage import default_db_path, keychain, spend

    now = datetime(2026, 11, 8, tzinfo=UTC)
    meter = default_db_path().with_name(spend.FILENAME)
    keychain.set_item(keychain.GEMINI_SERVICE, "fake-key", keychain.GEMINI_ACCOUNT)
    try:
        assert "nothing spent yet" in status.cloud_line(now)
        assert not meter.exists()  # a status never creates the meter
        spend.SpendStore(meter)
        assert status.cloud_line(now).startswith("£0.00 of £9 this month")
    finally:
        keychain.delete_item(keychain.GEMINI_SERVICE, keychain.GEMINI_ACCOUNT)
