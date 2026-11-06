"""Lloyds CSV Importer.

UNVERIFIED: the header, date format and row order below come from spec #1
(search results), not a real export. Confirm against a real export's header
row only and change them here; nothing else in the code assumes the layout.
"""

import csv
import hashlib
import re
from datetime import date, datetime
from decimal import Decimal

from finance_agent.ingest.types import ParsedFile, ParsedRow, RejectedRow

HEADER = (
    "Transaction Date",
    "Transaction Type",
    "Sort Code",
    "Account Number",
    "Transaction Description",
    "Debit Amount",
    "Credit Amount",
    "Balance",
)
DATE_FORMAT = "%d/%m/%Y"
OLDEST_FIRST = True  # row order; the Ledger stores rows in Bank order
CURRENCY = "GBP"

_AMOUNT = re.compile(r"\d+(\.\d{1,2})?")
_BALANCE = re.compile(r"-?\d+(\.\d{1,2})?")


def _minor(text: str) -> int:
    return int(Decimal(text) * 100)


def account_ref(sort_code: str, number: str) -> str:
    """Opaque internal reference; the full number never leaves `ingest`."""
    return hashlib.sha256(f"lloyds:{sort_code}:{number}".encode()).hexdigest()


def mask(number: str) -> str:
    return "••••" + number[-4:]


class _Malformed(ValueError):
    pass


def _parse_row(cells: list[str], line: int) -> ParsedRow:
    if len(cells) != len(HEADER):
        raise _Malformed("wrong number of columns")
    posted, typ, sort_code, number, desc, debit, credit, balance = (
        c.strip() for c in cells
    )
    try:
        posted_on: date = datetime.strptime(posted, DATE_FORMAT).date()
    except ValueError:
        raise _Malformed("invalid date") from None
    if bool(debit) == bool(credit):
        raise _Malformed("needs exactly one of debit or credit")
    amount = debit or credit
    if not _AMOUNT.fullmatch(amount):
        raise _Malformed("invalid amount")
    if not _BALANCE.fullmatch(balance):
        raise _Malformed("invalid balance")
    if not number.isdigit() or not desc:
        raise _Malformed("missing account number or description")
    signed = -_minor(debit) if debit else _minor(credit)
    return ParsedRow(
        line=line,
        account_ref=account_ref(sort_code, number),
        masked_number=mask(number),
        posted_on=posted_on,
        bank_type=typ,
        description=desc,
        amount_minor=signed,
        balance_minor=_minor(balance),
        currency=CURRENCY,
    )


def _body(text: str) -> tuple[int, list[str]]:
    """Skip leading `#` comment lines; return (lines skipped, remaining lines)."""
    lines = text.splitlines()
    skip = 0
    while skip < len(lines) and lines[skip].startswith("#"):
        skip += 1
    return skip, lines[skip:]


class Lloyds:
    bank = "Lloyds"

    def detects(self, text: str) -> bool:
        _, lines = _body(text)
        if not lines:
            return False
        header = next(csv.reader([lines[0]]))
        return tuple(c.strip() for c in header) == HEADER

    def parse(self, text: str) -> ParsedFile:
        skip, lines = _body(text)
        rows: list[ParsedRow] = []
        rejected: list[RejectedRow] = []
        for i, cells in enumerate(csv.reader(lines[1:])):
            line = skip + 2 + i  # 1-based, after the header line
            if not any(c.strip() for c in cells):
                continue
            try:
                rows.append(_parse_row(cells, line))
            except _Malformed as e:
                rejected.append(RejectedRow(line, str(e)))
        if not OLDEST_FIRST:
            rows.reverse()
        return ParsedFile(self.bank, rows, rejected)
