"""Ingest: one Importer per Bank, chosen by the file's header row.
Full account numbers stay inside this module."""

from finance_agent.ingest.lloyds import Lloyds
from finance_agent.ingest.types import Importer, ParsedFile, ParsedRow, RejectedRow

IMPORTERS: list[Importer] = [Lloyds()]


class UnknownFormat(ValueError):
    pass


class UnreadableFile(ValueError):
    """The file cannot be read as text. Carries a line number, never content."""

    def __init__(self, line: int, reason: str) -> None:
        super().__init__(f"line {line}: {reason}")
        self.line = line
        self.reason = reason


def parse(data: bytes) -> ParsedFile:
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
