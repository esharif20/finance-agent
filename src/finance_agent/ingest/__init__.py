"""Ingest: one Importer per Bank, chosen by the file's header row.
Full account numbers stay inside this module."""

from finance_agent.ingest.lloyds import Lloyds
from finance_agent.ingest.types import Importer, ParsedFile, ParsedRow, RejectedRow

IMPORTERS: list[Importer] = [Lloyds()]


class UnknownFormat(ValueError):
    pass


def parse(data: bytes) -> ParsedFile:
    text = data.decode("utf-8-sig")
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
    "parse",
]
