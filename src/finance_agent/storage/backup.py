"""Light local backups to a registered external drive, and restore (#95).

A copy is one zip in a dedicated folder on the drive: the SQLCipher database
taken with SQLite's backup API (so still encrypted under the same key), the card
art and the backup config. It is written to a temp file, fsynced, renamed, then
read back and integrity-checked. The drive's manifest lists the copies this code
made; nothing else is ever deleted. Keys are never written to disk or logged."""

import fcntl
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from alembic.script import ScriptDirectory
from sqlcipher3 import dbapi2 as _dbapi  # pyright: ignore[reportMissingTypeStubs, reportUnknownVariableType]

from finance_agent.storage import keychain, open_db

sqlcipher: Any = _dbapi

DEFAULT_FOLDER = "FinanceAgent Backups"
MANIFEST = "manifest.json"
CONFIG = "backup.json"  # in the data dir: the volume UUID and folder name only
STATUS = "backup-status.json"  # in the data dir: when the last backup ran
DB_MEMBER = "ledger.db"
ART = "card-art"
DAILY, WEEKLY = 7, 4
STALE = timedelta(days=7)

_COPY = re.compile(r"fa-(\d{8}T\d{6}Z)\.zip")
_PENDING = re.compile(r"\.fa-\d{8}T\d{6}Z\.zip\.partial")
_ART_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_FOLDER = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
_UUID = re.compile(r"[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}")
_RECOVERY = re.compile(r"FA1-([0-9a-f]{64})-([0-9a-f]{64})", re.IGNORECASE)
_MIGRATIONS = Path(__file__).parent / "migrations"


class BackupError(ValueError):
    pass


class CorruptCopy(BackupError):
    pass


class UnsupportedSchema(BackupError):
    pass


class RestoreRefused(BackupError):
    pass


@dataclass(frozen=True)
class Config:
    volume_uuid: str
    folder: str


@dataclass(frozen=True)
class Status:
    last_backup_at: datetime | None
    last_result: str | None  # ok | not_connected | read_only | failed
    warning: bool  # registered, and no good copy in the last 7 days


# --- config and status (data dir) ---------------------------------------------


def _fsync(fd: int) -> None:
    """macOS fsync does not flush the drive's cache; F_FULLFSYNC does."""
    full = getattr(fcntl, "F_FULLFSYNC", None)
    try:
        if full is None:
            raise OSError
        fcntl.fcntl(fd, full)
    except OSError:
        os.fsync(fd)


def _fsync_dir(folder: Path) -> None:
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # some file systems (exFAT) refuse a directory fsync; the rename stands
    finally:
        os.close(fd)


def _write_json(path: Path, obj: object) -> None:
    """Whole or not at all: temp file, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
            fh.flush()
            _fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return cast(dict[str, Any], obj) if isinstance(obj, dict) else {}


def save_config(data_dir: Path, cfg: Config) -> None:
    if not _UUID.fullmatch(cfg.volume_uuid) or not _FOLDER.fullmatch(cfg.folder):
        raise BackupError("not a volume UUID or a plain folder name")
    _write_json(
        data_dir / CONFIG, {"volume_uuid": cfg.volume_uuid, "folder": cfg.folder}
    )


def config(data_dir: Path) -> Config | None:
    raw = _read_json(data_dir / CONFIG)
    uuid, folder = raw.get("volume_uuid"), raw.get("folder")
    if not isinstance(uuid, str) or not isinstance(folder, str):
        return None
    if not _UUID.fullmatch(uuid) or not _FOLDER.fullmatch(folder):
        return None
    return Config(uuid, folder)


def forget_config(data_dir: Path) -> None:
    (data_dir / CONFIG).unlink(missing_ok=True)


def _when(v: object) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def record(data_dir: Path, now: datetime, result: str) -> None:
    prev = _read_json(data_dir / STATUS)
    last = now.isoformat() if result == "ok" else prev.get("last_success_at")
    _write_json(
        data_dir / STATUS,
        {"last_success_at": last, "last_attempt_at": now.isoformat(), "result": result},
    )


def status(data_dir: Path, now: datetime) -> Status:
    raw = _read_json(data_dir / STATUS)
    last = _when(raw.get("last_success_at"))
    result = raw.get("result")
    stale = last is None or now - last > STALE
    return Status(
        last,
        result if isinstance(result, str) else None,
        config(data_dir) is not None and stale,
    )


def due(data_dir: Path, now: datetime, every: timedelta = timedelta(hours=24)) -> bool:
    last = status(data_dir, now).last_backup_at
    return last is None or now - last > every


# --- recovery key ---------------------------------------------------------------


def recovery_key() -> str:
    """Both Keychain secrets as one string for a password manager. Callers show
    it on screen only; it is never written to a file or a log."""
    db = keychain.secret(keychain.DB_SERVICE).hex()
    hmac = keychain.secret(keychain.HMAC_SERVICE).hex()
    return f"FA1-{db}-{hmac}"


def parse_recovery_key(text: str) -> tuple[bytes, bytes]:
    m = _RECOVERY.fullmatch(text.strip())
    if m is None:
        raise BackupError("that is not a recovery key")  # never echo the input
    return bytes.fromhex(m.group(1)), bytes.fromhex(m.group(2))


# --- manifest and retention --------------------------------------------------------


def copy_name(at: datetime) -> str:
    return f"fa-{at.astimezone(UTC):%Y%m%dT%H%M%SZ}.zip"


def _taken_at(name: str) -> datetime:
    m = _COPY.fullmatch(name)
    if m is None:  # callers pass only names the manifest check accepted
        raise BackupError("not a copy name")
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


@dataclass
class _Manifest:
    copies: list[str]
    pending: str | None


def _manifest(folder: Path) -> _Manifest:
    path = folder / MANIFEST
    if not path.exists():
        return _Manifest([], None)
    raw = _read_json(path)
    copies, pending = raw.get("copies", []), raw.get("pending")
    names = cast(list[object], copies) if isinstance(copies, list) else None
    if (
        names is None
        or not all(isinstance(n, str) and _COPY.fullmatch(n) for n in names)
        or not (
            pending is None
            or (isinstance(pending, str) and _PENDING.fullmatch(pending))
        )
    ):
        # refuse rather than guess: a tampered manifest must not steer deletion
        raise BackupError("the manifest on the backup drive is damaged; check it")
    return _Manifest([str(n) for n in names], pending)


def _save(folder: Path, m: _Manifest) -> None:
    _write_json(folder / MANIFEST, {"copies": m.copies, "pending": m.pending})


def copies(folder: Path) -> list[str]:
    """Copies listed in the manifest, newest first (some may be missing)."""
    return sorted(_manifest(folder).copies, key=_taken_at, reverse=True)


def missing(folder: Path) -> list[str]:
    return [n for n in copies(folder) if not (folder / n).is_file()]


def retained(names: list[str]) -> set[str]:
    """The newest copy of each of the 7 latest days, then the newest copy of each
    of the 4 latest ISO weeks before those days: 11 copies, none over 4 weeks
    older than the newest daily one when backups run daily."""
    ordered = sorted(names, key=_taken_at, reverse=True)
    keep: list[str] = []
    days: list[date] = []
    for n in ordered:
        d = _taken_at(n).date()
        if d not in days and len(days) < DAILY:
            days.append(d)
            keep.append(n)
    weeks: list[tuple[int, int]] = []
    for n in ordered:
        t = _taken_at(n)
        w = (t.isocalendar()[0], t.isocalendar()[1])
        if days and t.date() >= days[-1]:
            continue
        if w not in weeks and len(weeks) < WEEKLY:
            weeks.append(w)
            keep.append(n)
    return set(keep)


def prune(folder: Path) -> list[str]:
    """Delete listed copies outside retention; only manifest names, ever."""
    m = _manifest(folder)
    keep = retained(m.copies)
    gone = [n for n in m.copies if n not in keep]
    for n in gone:
        (folder / n).unlink(missing_ok=True)
    m.copies = [n for n in m.copies if n in keep]
    _save(folder, m)
    return gone


# --- taking and checking a copy ------------------------------------------------


def _connect(path: Path, key: bytes) -> Any:
    conn = sqlcipher.connect(str(path))
    conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")  # must be first
    return conn


def _art_files(data_dir: Path) -> list[Path]:
    art = data_dir / ART
    if not art.is_dir() or art.is_symlink():
        return []
    return sorted(
        p
        for p in art.iterdir()
        if _ART_NAME.fullmatch(p.name) and p.is_file() and not p.is_symlink()
    )


def _check(db: Path, key: bytes) -> str:
    """Open with `key`, run cipher_integrity_check and integrity_check; return
    the schema revision. Any failure is CorruptCopy, with no detail quoted."""
    bad = CorruptCopy("the copy is damaged or does not open with this key")
    try:
        conn = _connect(db, key)
    except sqlcipher.DatabaseError:
        raise bad from None
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        if conn.execute("PRAGMA cipher_integrity_check").fetchall():
            raise bad
        if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise bad
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlcipher.DatabaseError:
        raise bad from None
    finally:
        conn.close()
    if row is None:
        raise bad
    return str(row[0])


def _member_ok(name: str) -> bool:
    if name in (DB_MEMBER, CONFIG):
        return True
    head, _, tail = name.partition("/")
    return head == ART and bool(_ART_NAME.fullmatch(tail))


def _extract(copy: Path, work: Path) -> None:
    """Read every member back from the drive into `work`, refusing anything
    unexpected (no path can escape `work`)."""
    try:
        with zipfile.ZipFile(copy) as zf:
            names = zf.namelist()
            if DB_MEMBER not in names or not all(_member_ok(n) for n in names):
                raise CorruptCopy("the copy holds unexpected files")
            if zf.testzip() is not None:
                raise CorruptCopy("the copy is damaged")
            for n in names:
                dest = work / n
                dest.parent.mkdir(exist_ok=True)
                with zf.open(n) as src, dest.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
    except (zipfile.BadZipFile, FileExistsError, EOFError):
        raise CorruptCopy("the copy is damaged") from None


def _clear_pending(folder: Path, m: _Manifest) -> None:
    """A run that died mid-write left its temp file, named in the manifest."""
    if m.pending is not None:
        (folder / m.pending).unlink(missing_ok=True)
        m.pending = None
        _save(folder, m)


def take(db_path: Path, folder: Path, now: datetime) -> str:
    """Write one verified copy into `folder`; return its name. On any failure
    no partial copy is left behind (a crash leaves only the temp file, which
    the next run removes)."""
    name = copy_name(now)
    m = _manifest(folder)
    _clear_pending(folder, m)
    if name in m.copies or (folder / name).exists():
        raise BackupError("a copy with this name already exists")
    key = keychain.secret(keychain.DB_SERVICE)
    data = db_path.parent
    partial = folder / f".{name}.partial"
    m.pending = partial.name
    _save(folder, m)
    work = Path(tempfile.mkdtemp(dir=data, prefix=".backup-"))
    try:
        snap = work / DB_MEMBER
        src, dst = _connect(db_path, key), _connect(snap, key)
        try:
            src.backup(dst)  # a consistent copy, encrypted under the same key
        finally:
            dst.close()
            src.close()
        with partial.open("xb") as fh, zipfile.ZipFile(fh, "w") as zf:
            zf.write(snap, DB_MEMBER)
            for p in _art_files(data):
                zf.write(p, f"{ART}/{p.name}")
            if (data / CONFIG).is_file():
                zf.write(data / CONFIG, CONFIG)
            zf.close()
            fh.flush()
            _fsync(fh.fileno())
        os.replace(partial, folder / name)
        _fsync_dir(folder)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    m.pending = None
    m.copies.append(name)
    _save(folder, m)
    work = Path(tempfile.mkdtemp(dir=data, prefix=".backup-verify-"))
    try:
        _extract(folder / name, work)
        _check(work / DB_MEMBER, key)
    except (BackupError, OSError):
        (folder / name).unlink(missing_ok=True)
        m.copies.remove(name)
        _save(folder, m)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return name


def _known_revisions() -> set[str]:
    return {s.revision for s in ScriptDirectory(str(_MIGRATIONS)).walk_revisions()}


def restore(
    copy: Path, db_path: Path, recovery: tuple[bytes, bytes] | None = None
) -> None:
    """Verify `copy`, keep the current ledger aside as `.pre-restore`, swap the
    copy in and migrate it to head. Deletes nothing. With `recovery` (a new
    Mac), the keys go into the Keychain only after the copy opens with them."""
    pre = db_path.with_name(db_path.name + ".pre-restore")
    if pre.exists() or pre.is_symlink():
        raise RestoreRefused(f"{pre.name} already exists; move it somewhere safe first")
    services = (keychain.DB_SERVICE, keychain.HMAC_SERVICE)
    if recovery is not None:
        for service, k in zip(services, recovery, strict=True):
            held = keychain.get_text(service)
            if held is not None and held != k.hex():
                raise RestoreRefused(
                    "the Keychain already holds a different key; restore without "
                    "--recovery-key, or on a Mac without this app's keys"
                )
        key = recovery[0]
    else:
        key = keychain.secret(keychain.DB_SERVICE)
    if not copy.is_file():
        raise BackupError("no such copy")
    data = db_path.parent
    data.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=data, prefix=".restore-"))
    try:
        _extract(copy, work)
        if _check(work / DB_MEMBER, key) not in _known_revisions():
            raise UnsupportedSchema("the copy was made by a newer version of the app")
        if recovery is not None:
            for service, k in zip(services, recovery, strict=True):
                keychain.set_text(service, k.hex())
        if db_path.exists():
            os.replace(db_path, pre)
        os.replace(work / DB_MEMBER, db_path)
        for p in sorted((work / ART).glob("*")):  # never overwrite the user's art
            dest = data / ART / p.name
            if not dest.exists():
                dest.parent.mkdir(exist_ok=True)
                os.replace(p, dest)
        if (work / CONFIG).is_file() and not (data / CONFIG).exists():
            os.replace(work / CONFIG, data / CONFIG)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    open_db(db_path, create=False).dispose()  # runs migrations for an older copy
