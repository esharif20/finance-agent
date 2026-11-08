"""The demo's start-up and path guards (#296)."""

import sys
from pathlib import Path

import keyring
import pytest
from sqlalchemy.exc import OperationalError

from finance_agent import identity, storage
from finance_agent.demo import app as demo
from finance_agent.demo import guard
from finance_agent.demo.session import Visitor, Visitors, plain_engine

SECRET = "s" * 40


def env(root: Path | str, **over: str) -> dict[str, str]:
    return {"FA_DEMO": "1", "FA_DEMO_DIR": str(root), "FA_DEMO_SECRET": SECRET} | over


@pytest.mark.parametrize(
    "over",
    [
        {"FA_DEMO": ""},
        {"FA_DEMO": "0"},
        {"FA_DEMO": "true"},
        {"FA_DEMO_SECRET": ""},
        {"FA_DEMO_SECRET": "short"},
        {"FA_DEMO_DIR": ""},
        {"FA_DEMO_DIR": "../"},
        {"FA_DEMO_DIR": "demo"},
        {"FA_DEMO_DIR": "/var/demo/../.."},
        {"FA_DEMO_DIR": "/"},
        {"FA_DEMO_DIR": str(Path.home())},
        {"FA_DEMO_DIR": str(storage.DEFAULT_DB.parent)},
        {"FA_DEMO_DIR": str(storage.DEFAULT_DB.parent / "demo")},
        {"FA_DEMO_DIR": str(storage.DEFAULT_DB.parent.parent)},
    ],
)
def test_refuses_to_start(tmp_path: Path, over: dict[str, str]) -> None:
    with pytest.raises(guard.Refused):
        guard.config(env(tmp_path, **over))


def test_refuses_without_the_environment_at_all() -> None:
    with pytest.raises(guard.Refused):
        guard.config({})


def test_a_symlinked_demo_dir_into_the_real_data_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "demo"
    link.symlink_to(storage.DEFAULT_DB.parent, target_is_directory=True)
    with pytest.raises(guard.Refused):
        guard.config(env(link))


def test_refuses_without_personas(tmp_path: Path) -> None:
    held = keyring.get_keyring()
    try:
        with pytest.raises(guard.Refused):
            demo.build(env(tmp_path))
        (tmp_path / "personas" / "1.db").parent.mkdir(exist_ok=True)
        (tmp_path / "personas" / "1.db").write_bytes(b"")  # no 0.db
        with pytest.raises(guard.Refused):
            demo.build(env(tmp_path))
    finally:
        keyring.set_keyring(held)


def test_inside_refuses_paths_and_symlinks_outside(tmp_path: Path) -> None:
    root, outside = tmp_path / "demo", tmp_path / "secret.db"
    root.mkdir()
    outside.write_bytes(b"not yours")
    assert guard.inside(root, root / "a.db") == (root / "a.db").resolve()
    (root / "link.db").symlink_to(outside)
    for p in (root / ".." / "secret.db", root / "link.db", Path("/etc/passwd")):
        with pytest.raises(guard.Refused):
            guard.inside(root, p)


def test_a_visitor_file_symlinked_outside_is_never_opened(tmp_path: Path) -> None:
    root, outside = tmp_path / "demo", tmp_path / "real.db"
    (root / "personas").mkdir(parents=True)
    (root / "personas" / "0.db").write_bytes(b"persona")
    outside.write_bytes(b"real ledger")
    vs = Visitors(root)
    v = Visitor("c" * 32, 0)
    (root / "visitors" / f"{v.id}.db").symlink_to(outside)
    with pytest.raises(guard.Refused):
        vs.open(v)
    with pytest.raises(guard.Refused):
        vs.reset(v)
    assert outside.read_bytes() == b"real ledger"


def test_a_symlinked_visitors_or_personas_dir_is_refused(tmp_path: Path) -> None:
    root, elsewhere = tmp_path / "demo", tmp_path / "elsewhere"
    (elsewhere / "personas").mkdir(parents=True)
    (elsewhere / "personas" / "0.db").write_bytes(b"x")
    root.mkdir()
    (root / "personas").symlink_to(elsewhere / "personas", target_is_directory=True)
    (root / "visitors").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(guard.Refused):
        Visitors(root)
    (root / "visitors").unlink()
    with pytest.raises(guard.Refused):
        Visitors(root)
    assert (elsewhere / "personas" / "0.db").read_bytes() == b"x"


def test_a_bad_visitor_id_is_never_a_path(tmp_path: Path) -> None:
    (tmp_path / "personas").mkdir()
    vs = Visitors(tmp_path)
    for bad in ("../../etc/passwd", "a" * 31, "A" * 32, "a" * 32 + "/x"):
        with pytest.raises(ValueError):
            vs.path(Visitor(bad, 0))


def test_the_local_app_is_unchanged() -> None:
    assert identity.HOST == "127.0.0.1"
    assert identity.current_user() == 1
    import finance_agent.api

    assert "finance_agent.demo" not in finance_agent.api.__dict__


def test_importing_the_demo_needs_no_environment() -> None:
    """Only touching `app` builds it, so the import-linter and tests can import."""
    assert sys.modules["finance_agent.demo.app"] is demo
    with pytest.raises(guard.Refused):
        demo.app  # noqa: B018 - the environment has no FA_DEMO


def test_a_missing_visitor_file_is_never_created_empty(tmp_path: Path) -> None:
    engine = plain_engine(tmp_path / "evicted.db")
    with pytest.raises(OperationalError):
        engine.connect().close()
    assert not (tmp_path / "evicted.db").exists()
