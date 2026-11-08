"""The demo's start-up and path guards (ADR 0016): it runs only when told to,
holds every database inside FA_DEMO_DIR, and cannot reach the Keychain."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import keyring
import keyring.backend

from finance_agent import storage

SECRET_MIN = 32  # characters of FA_DEMO_SECRET


class Refused(RuntimeError):
    """The demo will not start, or will not open that path."""


@dataclass(frozen=True)
class Config:
    dir: Path  # resolved
    secret: bytes


def config(environ: Mapping[str, str] = os.environ) -> Config:
    """FA_DEMO=1, an absolute FA_DEMO_DIR away from the real ledger, and a
    FA_DEMO_SECRET long enough to sign cookies; otherwise refuse to start."""
    if environ.get("FA_DEMO") != "1":
        raise Refused("the demo starts only with FA_DEMO=1")
    raw, secret = environ.get("FA_DEMO_DIR", ""), environ.get("FA_DEMO_SECRET", "")
    if len(secret) < SECRET_MIN:
        raise Refused(f"FA_DEMO_SECRET must be at least {SECRET_MIN} characters")
    given = Path(raw)
    if not raw or not given.is_absolute() or ".." in given.parts:
        raise Refused("FA_DEMO_DIR must be an absolute path without '..'")
    root = given.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise Refused("FA_DEMO_DIR may not be / or the home directory")
    real = storage.DEFAULT_DB.parent.resolve()
    if root.is_relative_to(real) or real.is_relative_to(root):
        raise Refused("FA_DEMO_DIR may not overlap the real data directory")
    return Config(root, secret.encode())


def inside(root: Path, path: Path) -> Path:
    """`path` resolved (symlinks followed), or Refused if that lands outside
    `root`. Every demo database path passes through here before it is opened."""
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise Refused("refusing a database path outside FA_DEMO_DIR")
    return resolved


class KeychainRefused(RuntimeError):
    pass


class _NoKeychain(keyring.backend.KeyringBackend):
    """Every read or write fails: the demo holds no secrets in a Keychain."""

    priority = 1  # pyright: ignore[reportAssignmentType]

    def get_password(self, service: str, username: str) -> str | None:
        raise KeychainRefused("the demo never reads the Keychain")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise KeychainRefused("the demo never writes the Keychain")

    def delete_password(self, service: str, username: str) -> None:
        raise KeychainRefused("the demo never writes the Keychain")


def lock_keychain() -> None:
    """Replace keyring's backend process-wide, so even a code path that reaches
    `storage.keychain` (open_db, keyed_ref, backups) fails closed."""
    keyring.set_keyring(_NoKeychain())
