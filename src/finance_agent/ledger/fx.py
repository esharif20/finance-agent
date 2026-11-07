"""FX conversion with provenance (#156). Decimal only; rates are never fetched.

A foreign-currency Transaction counts in GBP totals through exactly one rate,
taken in this order:
1. `bank`: the Bank's own GBP amount on that Transaction;
2. `exchange`: the rate implied by the user's own currency exchanges (Revolut
   EXCHANGE legs linked as a Transfer), the latest on or before its date;
3. `user`: a rate the user entered, the latest dated on or before it.
With none of them the figure is left out of the total and shown as "rate
needed": never guessed.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

GBP = "GBP"
CURRENCY_CODE = re.compile(r"[A-Z]{3}", re.ASCII)
SOURCES = ("bank", "exchange", "user")  # the order a rate is taken in
MIN_RATE = Decimal("0.000001")  # GBP per unit; outside these it is a typo
MAX_RATE = Decimal("1000000")


class InvalidRate(ValueError):
    """Zero, negative, non-finite or absurd: never used in a total."""


@dataclass(frozen=True)
class Rate:
    currency: str  # the foreign currency, ISO 4217
    gbp_per_unit: Decimal
    on: date  # the date the rate is from
    source: str  # one of SOURCES


@dataclass(frozen=True)
class Conversion:
    """One foreign figure in GBP and the single rate that made it."""

    currency: str
    amount_minor: int
    gbp_minor: int
    rate: Rate
    transaction_id: int | None = None  # None for an Account balance


def check_rate(rate: Decimal) -> Decimal:
    if not rate.is_finite() or not MIN_RATE <= rate <= MAX_RATE:
        raise InvalidRate("a rate must be a positive number in a sane range")
    return rate


def convert(amount_minor: int, rate: Decimal) -> int:
    """`amount_minor` times `rate`, to whole minor units, half to even."""
    check_rate(rate)
    with localcontext() as ctx:
        ctx.prec = 50  # exact for any amount the Ledger can hold
        return int((Decimal(amount_minor) * rate).to_integral_value(ROUND_HALF_EVEN))


def pick(rates: Iterable[Rate], currency: str, on: date) -> Rate | None:
    """The `exchange` rate, else the `user` rate, latest on or before `on`."""
    best: dict[str, Rate] = {}
    for r in rates:
        if r.currency == currency and r.on <= on:
            if r.source not in best or r.on >= best[r.source].on:
                best[r.source] = r
    return best.get("exchange") or best.get("user")
