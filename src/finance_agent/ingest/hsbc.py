"""HSBC UK CSV Importer.

Layout from HSBC UK's public help pages on downloading transactions: a
header-less CSV of `date (DD/MM/YYYY), description, amount` (signed, negative is
money out), newest first. It has no balance and no account number. UNVERIFIED
against a real export: confirm the first-row shape only, and change it here.

Because there is no balance, `balance_minor` is 0 for every row (ADR 0006 says a
Bank without one needs its own decision; the sequence alignment still separates
twins). All HSBC rows share one Account.
"""

import csv
import hashlib
import re
from datetime import datetime
from decimal import Decimal

from finance_agent.ingest.types import ParsedFile, ParsedRow, RejectedRow, body

DATE_FORMAT = "%d/%m/%Y"
CURRENCY = "GBP"
_DATE = re.compile(r"\d{2}/\d{2}/\d{4}", re.ASCII)
_AMOUNT = re.compile(r"-?\d+(\.\d{1,2})?", re.ASCII)
ACCOUNT_REF = hashlib.sha256(b"hsbc:default").hexdigest()


class Hsbc:
    bank = "HSBC"

    def detects(self, text: str) -> bool:
        _, lines = body(text)
        if not lines:
            return False
        try:
            cells = next(csv.reader(lines[:1]))
        except csv.Error:
            return False
        return (
            len(cells) == 3
            and bool(_DATE.fullmatch(cells[0].strip()))
            and bool(_AMOUNT.fullmatch(cells[2].strip()))
        )

    def parse(self, text: str) -> ParsedFile:
        skip, lines = body(text)
        rows: list[ParsedRow] = []
        rejected: list[RejectedRow] = []
        reader = csv.reader(lines)
        line = skip + 1  # 1-based physical line where the next record starts
        while True:
            try:
                cells = next(reader)
            except StopIteration:
                break
            except csv.Error:
                rejected.append(RejectedRow(line, "malformed CSV"))
                break
            line, start = skip + 1 + reader.line_num, line
            if not any(c.strip() for c in cells):
                continue
            if len(cells) != 3:
                rejected.append(RejectedRow(start, "wrong number of columns"))
                continue
            posted, desc, amount = (c.strip() for c in cells)
            if not (_DATE.fullmatch(posted) and desc and _AMOUNT.fullmatch(amount)):
                rejected.append(
                    RejectedRow(start, "not a date, description, amount row")
                )
                continue
            try:
                posted_on = datetime.strptime(posted, DATE_FORMAT).date()
            except ValueError:
                rejected.append(RejectedRow(start, "invalid date"))
                continue
            rows.append(
                ParsedRow(
                    line=start,
                    account_ref=ACCOUNT_REF,
                    masked_number="HSBC",
                    posted_on=posted_on,
                    bank_type="",
                    description=desc,
                    amount_minor=int(Decimal(amount) * 100),
                    balance_minor=0,
                    currency=CURRENCY,
                )
            )
        rows.reverse()  # Bank order, oldest first
        return ParsedFile(self.bank, rows, rejected)
