"""The user's own card images (#58), read from `card-art/` in the data dir.

Real bank art and logos only ever come from these local files: the repo ships
generic faces. Only allowlisted names, image bytes and a size limit are served."""

import re
from pathlib import Path

from finance_agent.storage import default_db_path

ART_KEYS = ("lloyds", "hsbc", "revolut")
MAX_BYTES = 2 * 1024 * 1024
_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")

# One place maps a Bank's name to its art. CSV imports name the Bank ("Lloyds");
# SimpleFIN gives org.name ("HSBC UK") or, without one, org.id. org.domain is null
# in practice, but a domain such as "lloydsbank.com" maps too.
_PATTERNS = {
    "lloyds": re.compile(r"\blloyds"),
    "hsbc": re.compile(r"\bhsbc"),
    "revolut": re.compile(r"\brevolut"),
}


class Refused(ValueError):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def art_key(bank: str) -> str | None:
    name = bank.strip().lower()
    return next((k for k, p in _PATTERNS.items() if p.search(name)), None)


def art_dir() -> Path:
    return default_db_path().parent / "card-art"


def _media_type(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def read(key: str) -> tuple[bytes, str]:
    """The image for an allowlisted key, or `Refused` (404 missing, 413, 415)."""
    if key not in ART_KEYS:
        raise Refused(404)
    folder = art_dir().resolve()
    for ext in _EXTENSIONS:
        path = folder / f"{key}{ext}"
        if not path.is_file():
            continue
        if path.resolve().parent != folder:  # a symlink out of the folder
            raise Refused(404)
        if path.stat().st_size > MAX_BYTES:
            raise Refused(413)
        data = path.read_bytes()
        media = _media_type(data[:12])
        if media is None:
            raise Refused(415)
        return data, media
    raise Refused(404)
