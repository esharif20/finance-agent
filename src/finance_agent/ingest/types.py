from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True)
class ParsedRow:
    line: int  # 1-based line in the file
    account_ref: str  # opaque; never the account number
    masked_number: str
    posted_on: date
    bank_type: str
    description: str
    amount_minor: int  # signed; negative is money out
    balance_minor: int  # the Bank's running balance after this row
    currency: str


@dataclass(frozen=True)
class RejectedRow:
    line: int
    reason: str  # never contains amounts or descriptions


@dataclass(frozen=True)
class ParsedFile:
    bank: str
    rows: list[ParsedRow]  # Bank order, oldest first
    rejected: list[RejectedRow]


def body(text: str) -> tuple[int, list[str]]:
    """Skip leading `#` comment lines; return (lines skipped, remaining lines)."""
    lines = text.splitlines()
    skip = 0
    while skip < len(lines) and lines[skip].startswith("#"):
        skip += 1
    return skip, lines[skip:]


class Importer(Protocol):
    bank: str

    def detects(self, text: str) -> bool: ...

    def parse(self, text: str) -> ParsedFile: ...
