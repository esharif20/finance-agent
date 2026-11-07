"""`fa backup` and `fa restore` (#95): light backups to one registered external
drive. The drive is found by its Volume UUID, never its name; if it is not
mounted, or mounted read-only, the run skips and never writes elsewhere. A
failed run is not retried in a loop: the next sync or mount tries once more.

The recovery key is shown once by `fa backup setup` and read from stdin by
`fa restore --recovery-key`; it never goes in argv, a file, a log or an error."""

import getpass
import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from finance_agent.cli import watch
from finance_agent.storage import MissingKey, default_db_path
from finance_agent.storage import backup as store

LABEL = "com.finance-agent.backup"
VOLUMES_DIR = Path("/Volumes")
NOT_CONNECTED = "backup drive not connected; skipped (it never writes elsewhere)"


@dataclass(frozen=True)
class Volume:
    uuid: str
    name: str
    mount: Path
    internal: bool
    writable: bool


def diskutil_volumes() -> list[Volume]:
    """Mounted volumes as `diskutil info -plist` reports them."""
    found: list[Volume] = []
    for mount in sorted(VOLUMES_DIR.iterdir()):
        if mount.is_symlink() or not os.path.ismount(mount):
            continue
        r = subprocess.run(  # noqa: S603
            ["/usr/sbin/diskutil", "info", "-plist", str(mount)],
            capture_output=True,
            check=False,
        )
        if r.returncode:
            continue
        info = plistlib.loads(r.stdout)
        uuid = info.get("VolumeUUID")
        if not isinstance(uuid, str):
            continue
        found.append(
            Volume(
                uuid=uuid,
                name=str(info.get("VolumeName", mount.name)),
                mount=Path(str(info.get("MountPoint") or mount)),
                internal=info.get("Internal", True) is not False,
                writable=info.get("WritableVolume", False) is True,
            )
        )
    return found


VOLUMES: Callable[[], list[Volume]] = diskutil_volumes  # tests install a fake


def _data_dir() -> Path:
    return default_db_path().parent


def setup(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> int:
    vols = [v for v in VOLUMES() if not v.internal]
    if not vols:
        print("fa: no external drive is mounted (the internal disk cannot be used)")
        return 1
    print("External drives:")
    for i, v in enumerate(vols, 1):
        print(f"  {i}. {v.name} ({v.mount})")
    choice = input("Register which drive? Number: ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(vols):
        print("fa: no such drive; nothing registered")
        return 1
    vol = vols[int(choice) - 1]
    store.save_config(_data_dir(), store.Config(vol.uuid, store.DEFAULT_FOLDER))
    print(
        f"Registered {vol.name} by its Volume UUID; "
        f"copies go in '{store.DEFAULT_FOLDER}'."
    )
    try:
        key = store.recovery_key()
    except MissingKey:
        print("No ledger yet, so no recovery key: run this again after `fa import`.")
    else:
        print(
            "Recovery key: store it in your password manager now. It is not saved "
            "anywhere and not shown again; `fa restore --recovery-key` needs it on a "
            "new Mac."
        )
        print(f"  {key}")
    watch.write_agent(
        LABEL, ["backup", "--on-mount"], {"StartOnMount": True}, agents_dir, ctl
    )
    return 0


def remove(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> int:
    watch.remove_agent(LABEL, agents_dir, ctl)
    store.forget_config(_data_dir())
    print("Backup drive unregistered. Copies on the drive are left as they are.")
    return 0


def _registered_folder() -> Path | None:
    """The folder on the registered drive, or None if it is not mounted."""
    cfg = store.config(_data_dir())
    if cfg is None:
        return None
    vol = next((v for v in VOLUMES() if v.uuid == cfg.volume_uuid), None)
    if vol is None or vol.internal:
        return None
    return vol.mount / cfg.folder


def run(on_mount: bool = False, notify: watch.Notifier | None = None) -> int:
    data = _data_dir()
    cfg = store.config(data)
    if cfg is None:
        if on_mount or notify:  # launchd: no drive registered is not an error
            return 0
        print("fa: no backup drive registered; run `fa backup setup`", file=sys.stderr)
        return 1
    now = datetime.now(UTC)
    if on_mount and not store.due(data, now):
        return 0
    vol = next((v for v in VOLUMES() if v.uuid == cfg.volume_uuid), None)
    if vol is None or vol.internal:
        store.record(data, now, "not_connected")
        print(f"fa: {NOT_CONNECTED}", file=sys.stderr)
        return 1
    if not vol.writable or not os.access(vol.mount, os.W_OK):
        store.record(data, now, "read_only")
        print("fa: backup drive is mounted read-only; skipped", file=sys.stderr)
        return 1
    folder = vol.mount / cfg.folder
    try:
        if folder.is_symlink() or not folder.resolve().is_relative_to(
            vol.mount.resolve()
        ):
            raise store.BackupError(
                "the backup folder is a link; refusing to follow it"
            )
        folder.mkdir(exist_ok=True)
        name = store.take(default_db_path(), folder, now)
        lost = store.missing(folder)
        gone = store.prune(folder)
    except (OSError, ValueError, LookupError) as e:
        store.record(data, now, "failed")
        why = f": {e}" if isinstance(e, store.BackupError) else ""  # ours: no data
        print(
            f"fa: backup failed ({type(e).__name__}{why}); no partial copy was "
            "kept, and the next run tries again",
            file=sys.stderr,
        )
        if notify:
            notify(watch.TITLE, "Backup failed; the next run tries again")
        return 1
    store.record(data, now, "ok")
    print(f"Backed up to {vol.name}: {name} ({len(gone)} old copies pruned)")
    if lost:
        # never "repaired": they stay listed and age out by retention
        print(
            f"Warning: {len(lost)} copies in the manifest are missing: "
            + ", ".join(lost)
        )
        if notify:
            notify(watch.TITLE, f"{len(lost)} backup copies are missing from the drive")
    return 0


def _read_recovery_key() -> tuple[bytes, bytes]:
    text = (
        getpass.getpass("Recovery key (input hidden): ")
        if sys.stdin.isatty()
        else sys.stdin.readline()
    )
    return store.parse_recovery_key(text)


def restore(copy: str | None, recovery: bool) -> int:
    rec = _read_recovery_key() if recovery else None
    if copy is not None and Path(copy).is_file():
        path = Path(copy)
    else:
        folder = _registered_folder()
        if folder is None:
            print(
                f"fa: {NOT_CONNECTED.split(';')[0]}; or pass the path of a copy",
                file=sys.stderr,
            )
            return 1
        names = [n for n in store.copies(folder) if (folder / n).is_file()]
        if copy is not None:
            names = [n for n in names if n == copy]
        if not names:
            print("fa: no such copy on the backup drive", file=sys.stderr)
            return 1
        path = folder / names[0]
    db = default_db_path()
    store.restore(path, db, rec)
    print(f"Restored {path.name}.")
    pre = db.with_name(db.name + ".pre-restore")
    if pre.exists():
        print(f"The previous ledger is kept as {pre}; delete it yourself when happy.")
    return 0
