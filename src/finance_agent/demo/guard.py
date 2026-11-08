"""The demo's start-up and path guards (ADR 0016): it runs only when told to,
holds every database inside FA_DEMO_DIR, and cannot reach the Keychain."""

import os
import secrets
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import keyring
import keyring.backend

from finance_agent import storage

SECRET_MIN = 32  # characters of FA_DEMO_SECRET
VERCEL_DIR = "/tmp/fa-demo"  # noqa: S108 - Vercel: /tmp is per-instance, the only writable path


class Refused(RuntimeError):
    """The demo will not start, or will not open that path."""


@dataclass(frozen=True)
class Config:
    dir: Path  # resolved
    secret: bytes
    seed: Path | None = None  # read-only Personas copied into dir/personas


def config(environ: Mapping[str, str] = os.environ) -> Config:
    """FA_DEMO=1, an absolute FA_DEMO_DIR away from the real ledger, and a
    FA_DEMO_SECRET long enough to sign cookies; otherwise refuse to start.
    On Vercel (VERCEL=1) FA_DEMO_DIR defaults to VERCEL_DIR; an optional
    FA_DEMO_SEED names the read-only Personas that `seed` copies there."""
    if environ.get("FA_DEMO") != "1":
        raise Refused("the demo starts only with FA_DEMO=1")
    secret = environ.get("FA_DEMO_SECRET", "")
    if len(secret) < SECRET_MIN:
        raise Refused(
            f"FA_DEMO_SECRET is missing or shorter than {SECRET_MIN} characters:"
            " set a random per-deployment value (see docs/DEPLOY.md)"
        )
    raw = environ.get("FA_DEMO_DIR") or (VERCEL_DIR if environ.get("VERCEL") else "")
    root = _safe("FA_DEMO_DIR", raw)
    seed = environ.get("FA_DEMO_SEED")
    return Config(root, secret.encode(), _safe("FA_DEMO_SEED", seed) if seed else None)


def _safe(name: str, raw: str) -> Path:
    """`raw` resolved: absolute, no '..', not / or home, away from the real data."""
    given = Path(raw)
    if not raw or not given.is_absolute() or ".." in given.parts:
        raise Refused(f"{name} must be an absolute path without '..'")
    root = given.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise Refused(f"{name} may not be / or the home directory")
    real = storage.DEFAULT_DB.parent.resolve()
    if root.is_relative_to(real) or real.is_relative_to(root):
        raise Refused(f"{name} may not overlap the real data directory")
    return root


def seed(cfg: Config) -> None:
    """Copy each bundled Persona missing from FA_DEMO_DIR/personas (a cold
    start on Vercel, whose bundle is read-only), atomically, file by file."""
    if cfg.seed is None:
        return
    out = cfg.dir / "personas"
    out.mkdir(parents=True, exist_ok=True)
    inside(cfg.dir, out)
    for src in sorted(cfg.seed.glob("*.db")):
        dst = inside(cfg.dir, out / src.name)
        if dst.is_file():
            continue
        tmp = dst.with_suffix(f".{secrets.token_hex(4)}.tmp")
        shutil.copyfile(inside(cfg.seed, src), tmp)
        os.replace(tmp, dst)


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
