"""One run at a time, by an flock on a file in the data dir. The OS drops the
lock when a run dies, so a crash never blocks the next. Shared by backup and sync."""

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


class Busy(Exception):
    pass


@contextmanager
def exclusive(data_dir: Path, name: str) -> Generator[None]:
    """Hold the lock `name` in `data_dir`, or raise Busy at once."""
    data_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(data_dir / name, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy from None
        yield
    finally:
        os.close(fd)
