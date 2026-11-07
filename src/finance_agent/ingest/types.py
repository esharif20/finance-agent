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
    # the Bank's running balance after this row; None when the export has none
    # (HSBC): stored NULL like a synced row, so it is never reconciled
    balance_minor: int | None
    currency: str
    account_name: str = ""  # the new Account's name; "" means "Account <masked>"
    # the GBP the Bank charged for a foreign-currency row, when its export says
    # (#156 rate source 1); no supported export carries it yet
    gbp_minor: int | None = None


@dataclass(frozen=True)
class RejectedRow:
    line: int
    reason: str  # never contains amounts or descriptions


@dataclass(frozen=True)
class ParsedFile:
    bank: str
    rows: list[ParsedRow]  # Bank order, oldest first
    rejected: list[RejectedRow]
    skipped: int = 0  # rows left out by design (Revolut PENDING/REVERTED); a count


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
