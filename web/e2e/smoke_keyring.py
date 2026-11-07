"""Throwaway keyring backend for the smoke run only (web/e2e/smoke.mjs selects it
through PYTHON_KEYRING_BACKEND). The CI runner has no keychain, and the smoke test
must never touch the login Keychain. Items live in a JSON file inside the smoke
temp directory, so the separate `fa` processes of one run share them."""

import json
import os
import tempfile
from pathlib import Path

import keyring.backend

FILE_ENV = "FA_SMOKE_KEYRING_FILE"


class SmokeKeyring(keyring.backend.KeyringBackend):
    priority = 1  # pyright: ignore[reportAssignmentType]

    def _path(self) -> Path:
        raw = os.environ.get(FILE_ENV)
        if not raw:
            raise RuntimeError(
                f"{FILE_ENV} is not set; this backend is for the smoke run only"
            )
        path = Path(raw).resolve()
        if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise RuntimeError("smoke keyring file must live under the temp directory")
        return path

    def _load(self) -> dict[str, str]:
        path = self._path()
        return json.loads(path.read_text()) if path.exists() else {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._load().get(f"{service}\0{username}")

    def set_password(self, service: str, username: str, password: str) -> None:
        items = self._load()
        items[f"{service}\0{username}"] = password
        self._path().write_text(json.dumps(items))

    def delete_password(self, service: str, username: str) -> None:
        items = self._load()
        items.pop(f"{service}\0{username}", None)
        self._path().write_text(json.dumps(items))
