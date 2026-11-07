"""Synthetic HSBC UK and Revolut exports (no real data, ever). Integer pence only.

HSBC UK: header-less `DD/MM/YYYY,description,amount`, newest first.
Revolut: `Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,
State,Balance`, oldest first. Both start with `# synthetic` (fixture guard).
"""

import random
from datetime import date, timedelta

REVOLUT_HEADER = (
    "Type,Product,Started Date,Completed Date,Description,Amount,Fee,Currency,"
    "State,Balance"
)
_DESCS = ["TESCO 3214", "PRET 0231", "SPOTIFY", "TFL TRAVEL", "ACME LTD SALARY"]
START = date(2026, 1, 1)


def money(minor: int) -> str:
    sign = "-" if minor < 0 else ""
    return f"{sign}{abs(minor) // 100}.{abs(minor) % 100:02d}"


def hsbc_export(seed: int, n: int = 20) -> str:
    rng = random.Random(seed)  # noqa: S311
    rows = [
        f"{(START + timedelta(days=i // 2)):%d/%m/%Y},{rng.choice(_DESCS)},"
        f"{money(rng.randint(-9000, 9000) or 1)}"
        for i in range(n)
    ]
    return "# synthetic\n" + "\n".join(reversed(rows)) + "\n"


def revolut_export(
    seed: int,
    n: int = 20,
    *,
    currency: str = "GBP",
    state: str = "COMPLETED",
    desc: str | None = None,
) -> str:
    rng = random.Random(seed)  # noqa: S311
    balance = 100_000
    out = ["# synthetic", REVOLUT_HEADER]
    for i in range(n):
        amount = rng.randint(-9000, 9000) or 1
        fee = rng.choice([0, 0, 25])
        balance += amount - fee
        day = START + timedelta(days=i)
        when = f"{day:%Y-%m-%d} 10:{i % 60:02d}:00"
        out.append(
            f"CARD_PAYMENT,Current,{when},{when},{desc or rng.choice(_DESCS)},"
            f"{money(amount)},{money(fee)},{currency},{state},{money(balance)}"
        )
    return "\n".join(out) + "\n"
