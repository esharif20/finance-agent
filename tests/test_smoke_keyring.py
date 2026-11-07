"""The smoke run's throwaway keyring is opt-in: nothing selects it by default,
and it refuses to work outside the smoke temp directory."""

import importlib
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "web" / "e2e"))
E2E = Path(__file__).resolve().parent.parent / "web" / "e2e"
SMOKE = (E2E / "smoke.mjs").read_text()
CODE = "import keyring; print(type(keyring.get_keyring()).__name__)"


def _backend(extra: dict[str, str]) -> str:
    env = {k: v for k, v in os.environ.items() if k != "PYTHON_KEYRING_BACKEND"}
    env["PYTHONPATH"] = str(E2E)
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", CODE],
        env={**env, **extra},
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def test_not_selected_unless_smoke_sets_it() -> None:
    assert _backend({}) != "SmokeKeyring"
    assert (
        _backend({"PYTHON_KEYRING_BACKEND": "smoke_keyring.SmokeKeyring"})
        == "SmokeKeyring"
    )


def test_smoke_script_sets_it_for_its_children() -> None:
    assert "PYTHON_KEYRING_BACKEND" in SMOKE and "smoke_keyring.SmokeKeyring" in SMOKE


def _load(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    del monkeypatch
    return importlib.import_module("smoke_keyring")


def test_round_trip_in_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _load(monkeypatch)
    kr = mod.SmokeKeyring()
    monkeypatch.setenv(mod.FILE_ENV, str(tmp_path / "k.json"))
    assert kr.get_password("s", "u") is None
    kr.set_password("s", "u", "v")
    assert kr.get_password("s", "u") == "v"
    kr.delete_password("s", "u")
    assert kr.get_password("s", "u") is None


def test_refuses_without_file_or_outside_temp(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load(monkeypatch)
    monkeypatch.delenv(mod.FILE_ENV, raising=False)
    with pytest.raises(RuntimeError):
        mod.SmokeKeyring().get_password("s", "u")
    monkeypatch.setenv(mod.FILE_ENV, str(Path.home() / "k.json"))
    with pytest.raises(RuntimeError):
        mod.SmokeKeyring().get_password("s", "u")
