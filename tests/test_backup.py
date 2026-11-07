"""#95: light backups to a registered external drive, and `fa restore`.

Temp dirs, a fake volume resolver, the in-memory keychain and a fake launchctl
only: no test touches a real volume, the real Keychain or diskutil."""

import io
import json
import logging
import os
import plistlib
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import keyring
import pytest
from hypothesis import HealthCheck, given, settings

from conftest import MemoryKeyring
from finance_agent import storage
from finance_agent.cli import backup, main, watch
from finance_agent.ledger import Ledger
from finance_agent.storage import backup as store
from finance_agent.storage import keychain, open_db
from synthetic_lloyds import (
    MAX_ROWS,
    History,
    generate_histories,
    histories,
    render_export,
)

UUID = "0A1B2C3D-0000-4000-8000-000000000001"
OTHER = "0A1B2C3D-0000-4000-8000-000000000002"
FOLDER = store.DEFAULT_FOLDER


def _no_launchctl(_args: list[str]) -> None:
    pass


def _answer_one(_prompt: str) -> str:
    return "1"


def _synced(_user: int, _notify: object) -> int:
    return 0


def _is_mount(_path: object) -> bool:
    return True


def _vol(mount: Path, uuid: str = UUID, **kw: object) -> backup.Volume:
    mount.mkdir(parents=True, exist_ok=True)
    args: dict[str, object] = {
        "uuid": uuid,
        "name": "SYNTH DRIVE",
        "mount": mount,
        "internal": False,
        "writable": True,
    }
    args.update(kw)
    return backup.Volume(**args)  # type: ignore[arg-type]


@pytest.fixture
def drives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[backup.Volume]:
    """The fake resolver's mounted volumes; tests edit the list."""
    vols: list[backup.Volume] = []
    monkeypatch.setattr(backup, "VOLUMES", lambda: list(vols))
    monkeypatch.setattr(watch, "AGENTS_DIR", tmp_path / "LaunchAgents")
    monkeypatch.setattr(watch, "_launchctl", _no_launchctl)
    return vols


@pytest.fixture
def ledger_db() -> Iterator[Path]:
    """A synthetic Ledger at the (temp) default path."""
    path = storage.default_db_path()
    h, _ = generate_histories(seed=3, days=40)
    export = path.parent / "export.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    export.write_text(render_export(h, 0, len(h.txns)).text)
    engine = open_db(path)
    with engine.begin() as conn:
        Ledger(conn, 1).import_files([export])
    engine.dispose()
    export.unlink()
    yield path


def _snapshot(path: Path) -> tuple[object, object]:
    engine = open_db(path, create=False)
    try:
        with engine.begin() as conn:
            led = Ledger(conn, 1)
            return led.transactions(), led.accounts()
    finally:
        engine.dispose()


def _register(mount: Path, uuid: str = UUID) -> None:
    store.save_config(storage.default_db_path().parent, store.Config(uuid, FOLDER))


def _zips(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


# --- fa backup ---------------------------------------------------------------


def test_absent_drive_skips_and_records_it(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    _register(tmp_path / "Volumes" / "D")
    assert main(["backup"]) == 1
    st = store.status(ledger_db.parent, datetime.now(UTC))
    assert st.last_result == "not_connected" and st.last_backup_at is None
    assert st.warning  # registered but never backed up


def test_same_name_drive_with_another_uuid_is_refused(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    impostor = tmp_path / "Volumes" / "SYNTH DRIVE"
    drives.append(_vol(impostor, uuid=OTHER))
    _register(impostor)
    assert main(["backup"]) == 1
    assert list(impostor.iterdir()) == []  # nothing written to the impostor


def test_backup_writes_a_verified_copy_and_restore_gives_the_same_ledger(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    before = _snapshot(ledger_db)
    assert main(["backup"]) == 0
    folder = mount / FOLDER
    copies = store.copies(folder)
    assert len(copies) == 1 and _zips(folder) == sorted([copies[0], store.MANIFEST])
    st = store.status(ledger_db.parent, datetime.now(UTC))
    assert st.last_result == "ok" and st.last_backup_at and not st.warning
    with zipfile.ZipFile(folder / copies[0]) as zf:  # still SQLCipher-encrypted
        assert not zf.read(store.DB_MEMBER).startswith(b"SQLite format 3\x00")

    ledger_db.unlink()
    assert main(["restore"]) == 0
    assert _snapshot(ledger_db) == before


def test_read_only_drive_is_skipped(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount, writable=False))
    _register(mount)
    assert main(["backup"]) == 1
    assert list(mount.iterdir()) == []
    assert store.status(ledger_db.parent, datetime.now(UTC)).last_result == "read_only"


def test_an_interrupted_write_leaves_no_partial_copy(
    drives: list[backup.Volume],
    ledger_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    real_write = zipfile.ZipFile.write

    def unplugged(self: zipfile.ZipFile, *a: object, **k: object) -> None:
        real_write(self, *a, **k)  # type: ignore[arg-type]
        raise OSError("device not configured")

    monkeypatch.setattr(zipfile.ZipFile, "write", unplugged)
    assert main(["backup"]) == 1
    folder = mount / FOLDER
    assert store.copies(folder) == []
    assert [n for n in _zips(folder) if n != store.MANIFEST] == []
    assert store.status(ledger_db.parent, datetime.now(UTC)).last_result == "failed"
    monkeypatch.setattr(zipfile.ZipFile, "write", real_write)  # drive is back
    assert main(["backup"]) == 0
    assert len(store.copies(folder)) == 1


def test_a_crash_mid_write_is_cleaned_on_the_next_run(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    folder = mount / FOLDER
    folder.mkdir()
    partial = folder / ".fa-20260101T000000Z.zip.partial"
    partial.write_bytes(b"half a copy")
    (folder / store.MANIFEST).write_text(
        json.dumps({"copies": [], "pending": partial.name})
    )
    assert main(["backup"]) == 0
    assert not partial.exists() and len(store.copies(folder)) == 1


def test_backup_deletes_only_files_named_in_its_manifest(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    folder = mount / FOLDER
    folder.mkdir()
    old = [f"fa-202601{d:02d}T120000Z.zip" for d in range(1, 29)]
    stranger = folder / "fa-20250101T120000Z.zip"  # same pattern, not ours
    for n in [*old, stranger.name, "notes.txt"]:
        (folder / n).write_bytes(b"x")
    victim = tmp_path / "outside.txt"
    victim.write_text("keep me")
    (folder / store.MANIFEST).write_text(json.dumps({"copies": old, "pending": None}))
    assert main(["backup"]) == 0
    assert stranger.exists() and (folder / "notes.txt").exists()
    assert len(store.copies(folder)) == 11

    # a tampered manifest naming a path outside the folder is refused whole
    (folder / store.MANIFEST).write_text(
        json.dumps({"copies": ["../../outside.txt"], "pending": "../../outside.txt"})
    )
    assert main(["backup"]) == 1
    assert victim.read_text() == "keep me"


def test_missing_copies_are_warned_about_not_repaired(
    drives: list[backup.Volume],
    ledger_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    folder = mount / FOLDER
    folder.mkdir()
    gone = "fa-20260101T120000Z.zip"  # listed, but something else deleted it
    (folder / store.MANIFEST).write_text(
        json.dumps({"copies": [gone], "pending": None})
    )
    assert main(["backup"]) == 0
    out = capsys.readouterr().out
    assert "missing" in out and gone in out
    assert gone in store.copies(folder) and not (folder / gone).exists()


def test_retention_keeps_exactly_7_daily_and_4_weekly() -> None:
    start = datetime(2026, 8, 1, 12, tzinfo=UTC)
    names = [store.copy_name(start + timedelta(days=d)) for d in range(61)]
    names += [store.copy_name(start + timedelta(days=60, hours=-3))]  # same day
    kept = store.retained(names)
    days = sorted(n[3:11] for n in kept)
    # one copy a day to Wed 30 Sep: 24-30 Sep daily, then the newest copy of
    # each of the 4 ISO weeks before (23 Sep, 20 Sep, 13 Sep, 6 Sep)
    assert days == [
        "20260906",
        "20260913",
        "20260920",
        "20260923",
        *[f"202609{d}" for d in range(24, 31)],
    ]
    assert store.copy_name(start + timedelta(days=60)) in kept  # newest of the day


# --- restore -------------------------------------------------------------------


def _one_copy(drives: list[backup.Volume], tmp_path: Path) -> Path:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    assert main(["backup"]) == 0
    folder = mount / FOLDER
    return folder / store.copies(folder)[0]


def test_a_corrupted_copy_is_refused(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    copy = _one_copy(drives, tmp_path)
    original = ledger_db.read_bytes()
    with zipfile.ZipFile(copy) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    db = bytearray(members[store.DB_MEMBER])
    db[5000:5016] = bytes(16)  # a damaged page, re-zipped with a valid CRC
    members[store.DB_MEMBER] = bytes(db)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        for n, b in members.items():
            zf.writestr(n, b)
    assert main(["restore", str(bad)]) == 1
    raw = bytearray(copy.read_bytes())  # bytes flipped on the drive itself
    raw[len(raw) // 2] ^= 0xFF
    copy.write_bytes(bytes(raw))
    assert main(["restore", str(copy)]) == 1
    assert ledger_db.read_bytes() == original
    assert not ledger_db.with_name("ledger.db.pre-restore").exists()


def test_restore_keeps_the_current_db_aside_and_never_overwrites_it(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    copy = _one_copy(drives, tmp_path)
    current = ledger_db.read_bytes()
    assert main(["restore", str(copy)]) == 0
    pre = ledger_db.with_name("ledger.db.pre-restore")
    assert pre.read_bytes() == current
    assert main(["restore", str(copy)]) == 1  # would replace .pre-restore: refused
    assert pre.read_bytes() == current


def test_a_copy_from_a_newer_schema_is_refused(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    engine = open_db(ledger_db)
    with engine.begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num = '9999_future'")
    engine.dispose()
    copy = _one_copy(drives, tmp_path)
    with pytest.raises(store.UnsupportedSchema):
        store.restore(copy, ledger_db)


def test_a_zip_with_unexpected_members_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "fa-20260101T000000Z.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr(store.DB_MEMBER, b"x")
        zf.writestr("../escape.txt", b"x")
    with pytest.raises(store.CorruptCopy):
        store.restore(bad, storage.default_db_path())
    assert not (tmp_path / "escape.txt").exists()


# --- recovery key ----------------------------------------------------------------


@pytest.fixture
def new_mac() -> Iterator[Callable[[], None]]:
    """Wipe the in-memory Keychain to simulate a new Mac; restored afterwards."""
    kr = keyring.get_keyring()
    assert isinstance(kr, MemoryKeyring)
    items = kr.items
    saved = dict(items)

    def wipe() -> None:
        items.clear()

    yield wipe
    items.clear()
    items.update(saved)


def test_setup_registers_by_uuid_and_shows_the_recovery_key_once(
    drives: list[backup.Volume],
    ledger_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    drives.append(_vol(tmp_path / "Volumes" / "Internal", uuid=OTHER, internal=True))
    drives.append(_vol(tmp_path / "Volumes" / "D"))
    monkeypatch.setattr("builtins.input", _answer_one)
    assert main(["backup", "setup"]) == 0
    cfg = store.config(ledger_db.parent)
    assert cfg == store.Config(UUID, FOLDER)  # the internal disk was not offered
    out = capsys.readouterr().out
    assert store.recovery_key() in out and "Internal" not in out
    plist = (watch.AGENTS_DIR / f"{backup.LABEL}.plist").read_bytes()
    assert b"StartOnMount" in plist and b"--on-mount" in plist


def test_setup_refuses_when_only_the_internal_disk_is_mounted(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    drives.append(_vol(tmp_path / "Volumes" / "Mac", internal=True))
    assert main(["backup", "setup"]) == 1
    assert store.config(ledger_db.parent) is None


def test_restore_with_the_recovery_key_on_a_new_mac(
    drives: list[backup.Volume],
    ledger_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    new_mac: Callable[[], None],
) -> None:
    caplog.set_level(logging.DEBUG)
    copy = _one_copy(drives, tmp_path)
    before = _snapshot(ledger_db)
    key = store.recovery_key()
    ledger_db.unlink()
    new_mac()
    argv = ["restore", str(copy), "--recovery-key"]

    monkeypatch.setattr("sys.stdin", io.StringIO("FA1-" + "0" * 64 + "-" + "0" * 64))
    assert main(argv) == 1  # a wrong key is refused, and not echoed
    monkeypatch.setattr("sys.stdin", io.StringIO(key + "\n"))
    assert main(argv) == 0
    assert _snapshot(ledger_db) == before
    out = capsys.readouterr()
    for secret in (key, key[4:68], key[69:]):
        assert secret not in out.out + out.err
        assert secret not in caplog.text
        assert all(secret not in a for a in argv)
        for p in [*ledger_db.parent.rglob("*"), *copy.parent.rglob("*")]:
            if p.is_file():
                assert secret.encode() not in p.read_bytes()


def test_a_malformed_recovery_key_is_not_echoed(
    ledger_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("hunter2-not-a-key\n"))
    assert main(["restore", str(tmp_path / "x.zip"), "--recovery-key"]) == 1
    out = capsys.readouterr()
    assert "hunter2" not in out.out + out.err


# --- schedule hooks and status ---------------------------------------------------


def test_on_mount_backs_up_only_when_the_last_is_over_24h_old(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    assert main(["backup", "--on-mount"]) == 0
    folder = mount / FOLDER
    assert len(store.copies(folder)) == 1
    assert main(["backup", "--on-mount"]) == 0  # within 24h: nothing new
    assert len(store.copies(folder)) == 1
    data = ledger_db.parent
    store.record(data, datetime.now(UTC) - timedelta(hours=25), "ok")
    assert store.due(data, datetime.now(UTC))


def test_status_warns_after_7_days(ledger_db: Path, tmp_path: Path) -> None:
    data = ledger_db.parent
    now = datetime(2026, 9, 30, tzinfo=UTC)
    assert not store.status(data, now).warning  # not set up: no warning
    _register(tmp_path)
    store.record(data, now - timedelta(days=6), "ok")
    assert not store.status(data, now).warning
    store.record(data, now - timedelta(days=1), "not_connected")
    st = store.status(data, now + timedelta(days=2))
    assert st.warning and st.last_backup_at == now - timedelta(days=6)


def test_the_scheduled_sync_backs_up_after_success(
    drives: list[backup.Volume],
    ledger_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finance_agent.cli import bank

    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    monkeypatch.setattr(bank, "sync", _synced)
    assert main(["sync", "--notify"]) == 0
    assert len(store.copies(mount / FOLDER)) == 1
    monkeypatch.setattr(bank, "sync", _synced)
    assert main(["sync"]) == 0  # a manual sync does not back up
    assert len(store.copies(mount / FOLDER)) == 1


# --- property: backup then restore gives the same Ledger -------------------------


@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(h=histories())
def test_backup_then_restore_gives_the_same_ledger(
    h: History, new_mac: Callable[[], None]
) -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        db = root / "mac" / "ledger.db"
        db.parent.mkdir()
        export = root / "export.csv"
        export.write_text(render_export(h, 0, len(h.txns[:MAX_ROWS])).text)
        engine = open_db(db)
        with engine.begin() as conn:
            Ledger(conn, 1).import_files([export])
        engine.dispose()
        before = _snapshot(db)
        folder = root / "drive" / FOLDER
        folder.mkdir(parents=True)
        name = store.take(db, folder, datetime.now(UTC))
        key = store.parse_recovery_key(store.recovery_key())
        new_mac()
        other = root / "new-mac" / "ledger.db"
        store.restore(folder / name, other, key)
        assert _snapshot(other) == before
        assert keychain.get_text(keychain.DB_SERVICE) == key[0].hex()


def test_diskutil_resolver_reads_uuid_and_flags_without_touching_disks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vols = tmp_path / "Volumes"
    for n in ("D", "Mac"):
        (vols / n).mkdir(parents=True)
    (vols / "Link").symlink_to(vols / "Mac")
    infos: dict[str, dict[str, object]] = {
        str(vols / "D"): {
            "VolumeUUID": UUID,
            "VolumeName": "D",
            "MountPoint": str(vols / "D"),
            "Internal": False,
            "WritableVolume": True,
        },
        str(vols / "Mac"): {"VolumeUUID": OTHER, "VolumeName": "Mac"},
    }
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, plistlib.dumps(infos[argv[-1]]))

    monkeypatch.setattr(backup, "VOLUMES_DIR", vols)
    monkeypatch.setattr(os.path, "ismount", _is_mount)
    monkeypatch.setattr(subprocess, "run", fake_run)
    found = backup.diskutil_volumes()
    assert [(v.uuid, v.internal, v.writable) for v in found] == [
        (UUID, False, True),
        (OTHER, True, False),  # missing flags fail safe: internal, read-only
    ]
    assert len(calls) == 2  # the symlink was skipped
    assert all(c[:3] == ["/usr/sbin/diskutil", "info", "-plist"] for c in calls)


# --- verifier: adversarial inputs --------------------------------------------------


@pytest.mark.parametrize(
    "entry", ["../../victim.zip", "/Users/victim.zip", "fa-20260101T000000Z.zip/../x"]
)
def test_a_manifest_naming_a_path_outside_the_folder_deletes_nothing(
    tmp_path: Path, entry: str
) -> None:
    folder = tmp_path / "Volumes" / "D" / FOLDER
    folder.mkdir(parents=True)
    victim = tmp_path / "victim.zip"
    victim.write_bytes(b"not ours")
    (folder / store.MANIFEST).write_text(json.dumps({"copies": [entry]}))
    with pytest.raises(store.BackupError):
        store.prune(folder)
    assert victim.read_bytes() == b"not ours"


def test_a_recovery_key_on_argv_is_refused_before_anything_runs(
    ledger_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    key = store.recovery_key()
    stdin = io.StringIO("untouched\n")
    monkeypatch.setattr("sys.stdin", stdin)
    before = ledger_db.read_bytes()
    assert main(["restore", "--recovery-key", key]) == 1
    out = capsys.readouterr()
    assert "never pass the recovery key" in out.err
    assert key not in out.out + out.err
    assert stdin.read() == "untouched\n"  # not prompted: refused first
    assert ledger_db.read_bytes() == before


def test_a_second_backup_never_deletes_a_running_ones_temp_file(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    """A sync-triggered run and an on-mount run can start together."""
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    folder = mount / FOLDER
    folder.mkdir()
    running = folder / ".fa-20260101T000000Z.zip.partial"
    running.write_bytes(b"half written")
    (folder / store.MANIFEST).write_text(
        json.dumps({"copies": [], "pending": running.name})
    )
    with store.lock(ledger_db.parent):  # the first run holds it
        assert main(["backup"]) == 1
    assert running.read_bytes() == b"half written"
    assert main(["backup"]) == 0  # once it is free, the next run goes ahead


def test_a_ledger_that_cannot_be_read_is_recorded_as_a_failed_run(
    drives: list[backup.Volume], ledger_db: Path, tmp_path: Path
) -> None:
    mount = tmp_path / "Volumes" / "D"
    drives.append(_vol(mount))
    _register(mount)
    ledger_db.write_bytes(os.urandom(8192))  # not a database under this key
    assert main(["backup"]) == 1
    assert store.status(ledger_db.parent, datetime.now(UTC)).last_result == "failed"
    assert store.copies(mount / FOLDER) == []
