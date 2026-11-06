"""Deterministic synthetic Lloyds exports. Integer pence throughout; no floats.

Layout (spec #1, unverified): Transaction Date, Transaction Type, Sort Code,
Account Number, Transaction Description, Debit Amount, Credit Amount, Balance.
Rows are oldest first. Every output starts with `# synthetic` (fixture guard).
"""

import random
from dataclasses import dataclass
from datetime import date, timedelta

HEADER = [
    "Transaction Date",
    "Transaction Type",
    "Sort Code",
    "Account Number",
    "Transaction Description",
    "Debit Amount",
    "Credit Amount",
    "Balance",
]
MAX_ROWS = 150
SORT_CODE = "00-00-00"  # obviously fake
START = date(2026, 1, 1)

# (descriptor variants, typical pence range)
MERCHANTS = [
    (
        ["TESCO STORES 3214", "TESCO STORES 3214 LONDON GB", "TESCO EXP 0042"],
        (150, 6500),
    ),
    (["PRET A MANGER 0231", "PRET A MANGER LONDON"], (250, 1100)),
    (["AMZNMKTPLACE*2K4LL9", "AMAZON.CO.UK*RT55X", "AMZ*MKTPLACE 7HD2"], (500, 9000)),
    (["PAYPAL *SPOTIFY", "SPOTIFY P1A2B3"], (1099, 1099)),
    (["TFL TRAVEL CH", "TFL.GOV.UK/CP"], (170, 1650)),
    (["DELIVEROO 2BX9", "DELIVEROO.CO.UK"], (1200, 3800)),
]
PAYERS = ["ACME LTD SALARY", "SYNTH EMPLOYER BGC"]
TYPES = {"merchant": "DEB", "salary": "FPI", "refund": "DEB", "transfer": "TFR"}


@dataclass(frozen=True)
class Txn:
    date: date
    type: str
    description: str
    amount_minor: int  # signed: negative is a debit
    balance_minor: int  # running balance after this row


@dataclass(frozen=True)
class History:
    sort_code: str
    account_number: str
    txns: list[Txn]


@dataclass(frozen=True)
class Export:
    text: str
    malformed_line: int | None  # 1-based line number in `text`


def _purchase(rng: random.Random) -> tuple[str, int]:
    names, (lo, hi) = rng.choice(MERCHANTS)
    return rng.choice(names), rng.randint(lo, hi)


def _with_balances(events: list[tuple[date, str, str, int]], opening: int) -> list[Txn]:
    out: list[Txn] = []
    bal = opening
    for d, typ, desc, amt in events:
        bal += amt
        out.append(Txn(d, typ, desc, amt, bal))
    return out


def generate_histories(seed: int, days: int = 60) -> tuple[History, History]:
    """Two synthetic Accounts whose Transfers mirror each other."""
    rng = random.Random(seed)  # noqa: S311 - synthetic data, not security
    a: list[tuple[date, str, str, int]] = []
    b: list[tuple[date, str, str, int]] = []
    for i in range(days):
        d = START + timedelta(days=i)
        if i % 30 == 0:
            a.append((d, "FPI", rng.choice(PAYERS), rng.randint(150000, 300000)))
        for _ in range(rng.randint(0, 3)):
            desc, amt = _purchase(rng)
            a.append((d, "DEB", desc, -amt))
            if rng.random() < 0.15:  # identical same-day repeat
                a.append((d, "DEB", desc, -amt))
            if rng.random() < 0.1:
                a.append((d, "DEB", desc.split()[0] + " REFUND", amt))
        if i == 5:  # guarantee the awkward cases in any history of 8+ days
            a.append((d, "DEB", "PRET A MANGER 0231", -450))
            a.append((d, "DEB", "PRET A MANGER 0231", -450))
            a.append((d, "DEB", "AMAZON.CO.UK REFUND", 1999))
        if i % 10 == 2:
            amt = rng.randint(1000, 20000)
            a.append((d, "TFR", "TO SYNTH SAVER 00000002", -amt))
            b.append((d, "TFR", "FROM SYNTH CURRENT 00000001", amt))
    return (
        History(SORT_CODE, "00000001", _with_balances(a, 100000)),
        History(SORT_CODE, "00000002", _with_balances(b, 500000)),
    )


def _money(minor: int) -> str:
    q, r = divmod(abs(minor), 100)  # floor divmod would print -1p as -1.99
    return f"{'-' if minor < 0 else ''}{q}.{r:02d}"


def _row(h: History, t: Txn, balance: int) -> str:
    debit = _money(-t.amount_minor) if t.amount_minor < 0 else ""
    credit = _money(t.amount_minor) if t.amount_minor >= 0 else ""
    return ",".join(
        [
            t.date.strftime("%d/%m/%Y"),
            t.type,
            h.sort_code,
            h.account_number,
            f'"{t.description}"',
            debit,
            credit,
            _money(balance),
        ]
    )


def render_export(
    h: History,
    start: int,
    end: int,
    *,
    gap_at: int | None = None,
    gap_minor: int = 0,
    malformed_after: int | None = None,
) -> Export:
    """Rows [start, end) as a Lloyds CSV.

    gap_at: from that row (relative to start) the printed balance is offset by
    gap_minor, so one balance break appears there.
    malformed_after: insert one unparseable row after that many rows.
    """
    rows = h.txns[start:end]
    if len(rows) > MAX_ROWS:
        raise ValueError(f"export of {len(rows)} rows exceeds {MAX_ROWS}")
    lines = ["# synthetic", ",".join(HEADER)]
    bad: int | None = None
    for i, t in enumerate(rows):
        if malformed_after == i:
            lines.append('31/02/2026,DEB,00-00-00,00000001,"BROKEN ROW",abc,,n/a')
            bad = len(lines)
        offset = gap_minor if gap_at is not None and i >= gap_at else 0
        lines.append(_row(h, t, t.balance_minor + offset))
    if malformed_after is not None and bad is None:
        lines.append('31/02/2026,DEB,00-00-00,00000001,"BROKEN ROW",abc,,n/a')
        bad = len(lines)
    return Export("\n".join(lines) + "\n", bad)


def overlapping_windows(n: int, size: int, step: int) -> list[tuple[int, int]]:
    """Windows of at most `size` rows advancing by `step` (< size overlaps)."""
    out: list[tuple[int, int]] = []
    s = 0
    while True:
        e = min(s + size, n)
        out.append((s, e))
        if e == n:
            return out
        s += step
