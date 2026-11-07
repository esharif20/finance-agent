"""Ingest: one Importer per Bank, chosen by the file's header row.
Full account numbers stay inside this module."""

import zlib

from finance_agent.ingest.hsbc import Hsbc
from finance_agent.ingest.lloyds import Lloyds
from finance_agent.ingest.revolut import Revolut
from finance_agent.ingest.types import Importer, ParsedFile, ParsedRow, RejectedRow

IMPORTERS: list[Importer] = [Lloyds(), Revolut(), Hsbc()]
MAX_BYTES = 64 * 1024 * 1024  # after decompression; a gzip bomb stops here


class UnknownFormat(ValueError):
    pass


class UnreadableFile(ValueError):
    """The file cannot be read as text. Carries a line number, never content."""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"line {line}: {reason}")
        self.line = line
        self.reason = reason


def _gunzip(data: bytes) -> bytes:
    d = zlib.decompressobj(31)
    try:
        out = d.decompress(data, MAX_BYTES + 1)
    except zlib.error:
        raise UnreadableFile(1, "not a valid gzip file") from None
    if len(out) > MAX_BYTES:
        raise UnreadableFile(1, "file too large")
    if not d.eof:
        raise UnreadableFile(1, "truncated gzip file")
    return out


def parse(data: bytes) -> ParsedFile:
    if data[:2] == b"\x1f\x8b":  # gzip; the Statement Import keeps the original bytes
        data = _gunzip(data)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        # The codec's own message quotes the offending byte; report the line only.
        line = data.count(b"\n", 0, e.start) + 1
        raise UnreadableFile(line, "not valid UTF-8 text") from None
    for imp in IMPORTERS:
        if imp.detects(text):
            return imp.parse(text)
    raise UnknownFormat("no Importer recognises this file's header")


__all__ = [
    "IMPORTERS",
    "Importer",
    "ParsedFile",
    "ParsedRow",
    "RejectedRow",
    "UnknownFormat",
    "UnreadableFile",
    "parse",
]
