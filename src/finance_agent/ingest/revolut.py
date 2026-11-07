"""Revolut CSV Importer.

Layout from Revolut's public help page on account statements: Type, Product,
Started Date, Completed Date, Description, Amount, Fee, Currency, State,
Balance; dates `YYYY-MM-DD HH:MM:SS`, oldest first. UNVERIFIED against a real
export: confirm the header row only, and change it here.

Each row keeps its own currency (#156): one Account per Product and currency,
since the Balance column runs per currency; a GBP Account keeps the ref it had
before FX. Amounts are hundredths in every currency, so a currency whose
minor unit is not a hundredth (JPY, KWD) is refused. Only COMPLETED rows are
imported; PENDING and REVERTED rows are never stored as settled, and the import
report counts them. The Fee column is separate from Amount, so a row's amount
is Amount - Fee, which keeps the Balance chain intact.
"""

import csv
import hashlib
import re
from datetime import datetime
from decimal import Decimal

from finance_agent.ingest.types import (
    NOT_HUNDREDTHS,
    NOT_HUNDREDTHS_REASON,
    ParsedFile,
    ParsedRow,
    RejectedRow,
    body,
)

HEADER = (
    "Type",
    "Product",
    "Started Date",
    "Completed Date",
    "Description",
    "Amount",
    "Fee",
    "Currency",
    "State",
    "Balance",
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MONEY = re.compile(r"-?\d+(\.\d{1,2})?", re.ASCII)
_DROPPED = {"PENDING", "REVERTED"}
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)


def _minor(text: str) -> int:
    return int(Decimal(text) * 100)


class _Malformed(ValueError):
    pass


def _parse_row(cells: list[str], line: int) -> ParsedRow | None:
    if len(cells) != len(HEADER):
        raise _Malformed("wrong number of columns")
    typ, product, started, completed, desc, amount, fee, cur, state, balance = (
        c.strip() for c in cells
    )
    if state in _DROPPED:
        return None
    if state != "COMPLETED":
        raise _Malformed("unknown state")
    if not _CURRENCY.fullmatch(cur):
        raise _Malformed("invalid currency")
    if cur in NOT_HUNDREDTHS:
        raise _Malformed(NOT_HUNDREDTHS_REASON)
    try:
        posted_on = datetime.strptime(completed or started, DATE_FORMAT).date()
    except ValueError:
        raise _Malformed("invalid date") from None
    fee = fee or "0"
    if not all(_MONEY.fullmatch(x) for x in (amount, fee, balance)):
        raise _Malformed("invalid amount, fee or balance")
    if not desc or not product:
        raise _Malformed("missing description or product")
    pocket = product if cur == "GBP" else f"{product}:{cur}"
    return ParsedRow(
        line=line,
        account_ref=hashlib.sha256(f"revolut:{pocket}".encode()).hexdigest(),
        masked_number=pocket.replace(":", " ")[:24],
        posted_on=posted_on,
        bank_type=typ,
        description=desc,
        amount_minor=_minor(amount) - _minor(fee),
        balance_minor=_minor(balance),
        currency=cur,
    )


class Revolut:
    bank = "Revolut"

    def detects(self, text: str) -> bool:
        _, lines = body(text)
        if not lines:
            return False
        try:
            header = next(csv.reader(lines[:1]))
        except csv.Error:
            return False
        return tuple(c.strip() for c in header) == HEADER

    def parse(self, text: str) -> ParsedFile:
        skip, lines = body(text)
        rows: list[ParsedRow] = []
        rejected: list[RejectedRow] = []
        skipped = 0
        reader = csv.reader(lines[1:])
        line = skip + 2
        while True:
            try:
                cells = next(reader)
            except StopIteration:
                break
            except csv.Error:
                rejected.append(RejectedRow(line, "malformed CSV"))
                break
            line, start = skip + 2 + reader.line_num, line
            if not any(c.strip() for c in cells):
                continue
            try:
                row = _parse_row(cells, start)
            except _Malformed as e:
                rejected.append(RejectedRow(start, str(e)))
                continue
            if row is None:  # PENDING or REVERTED
                skipped += 1
            else:
                rows.append(row)
        return ParsedFile(self.bank, rows, rejected, skipped)
