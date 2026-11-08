"""The privacy gateway's types (ADR 0011, THREAT_MODEL B5).

`ToolFact`s go in, real values and all. `GatewayPayload` is the only thing that
may leave the Mac: placeholders, closed-vocabulary words, generic Category names
and months, checked again here so a hand-built payload cannot carry more.
`PlaceholderMap` holds the real values for the local fill and refuses to be
serialised, pickled, iterated or printed."""

import re
from datetime import date
from decimal import Decimal
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)

# ---- what goes in (real values, local only) ---------------------------------


class Money(BaseModel):
    model_config = _STRICT
    minor: int  # signed integer minor units
    currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$")


class Percent(BaseModel):
    model_config = _STRICT
    value: Decimal  # 12.5 means 12.5%


class Month(BaseModel):
    model_config = _STRICT
    year: int = Field(ge=1, le=9999)
    month: int = Field(ge=1, le=12)


class Merchant(BaseModel):
    model_config = _STRICT
    name: str


class Category(BaseModel):
    model_config = _STRICT
    name: str


class Note(BaseModel):
    """A user's note (#124): left out unless the user opts in for the question."""

    model_config = _STRICT
    text: str


# A plain `int` is a count.
type Value = Money | Percent | Month | date | int | Merchant | Category | Note

# What a figure is, in words the code chose. Never a tool label: labels carry names.
type FieldName = Literal[
    "value",
    "amount",
    "total",
    "net",
    "average",
    "balance",
    "budget",
    "remaining",
    "limit",
    "change",
    "trend",
    "spending",
    "income",
    "money_in",
    "money_out",
    "fees",
    "refunds",
    "count",
    "share",
    "date",
    "month",
    "merchant",
    "category",
    "note",
]


class ToolFact(BaseModel):
    """One value from this turn's tool results, handed to `redact`."""

    model_config = _STRICT
    field: FieldName = "value"
    value: Value


# ---- what leaves ------------------------------------------------------------

# Every Category name the taxonomy has ever seeded (ledger.taxonomy, all versions;
# a test pins the two together). `gateway` imports nothing from the core, so the
# allowlist is spelt out here. A user-made Category is not generic: it leaves as {Cn}.
GENERIC_CATEGORIES: frozenset[str] = frozenset(
    {
        "Food & Drink", "Groceries", "Eating out", "Takeaway", "Coffee",
        "Transport", "Public transport", "Taxi", "Fuel", "Parking",
        "Shopping", "Online", "Clothing", "Electronics", "Household",
        "Bills & Utilities", "Bills", "Energy", "Water", "Phone & internet",
        "Council tax", "Rent or mortgage", "Phone & broadband", "Insurance",
        "Other bills", "Subscriptions", "Streaming", "Software", "AI tools",
        "Memberships", "Cloud storage", "Other subscriptions", "Housing", "Rent",
        "Mortgage", "Maintenance", "Health", "Pharmacy", "Fitness", "Medical",
        "Entertainment", "Events", "Games", "Hobbies", "Travel", "Flights",
        "Hotels", "Holidays", "Personal care", "Hair & beauty", "Toiletries",
        "Uncategorised",
    }
)  # fmt: skip

PLACEHOLDER = re.compile(r"\{([PMDC])([1-9][0-9]{0,3})\}")
MONTH_TEXT = re.compile(r"(?<![0-9])[0-9]{4}-(?:0[1-9]|1[0-2])(?![0-9])")
MAX_QUESTION = 500
MAX_NOTE = 200

type Kind = Literal[
    "money", "percent", "count", "date", "month", "merchant", "category"
]
type Size = Literal["tiny", "small", "medium", "large", "very large", "few", "many"]


def _check_free_text(text: str) -> str:
    """Free text leaves only as printable ASCII with no figure outside a
    placeholder or a `YYYY-MM` month."""
    if any(not (" " <= ch <= "~") for ch in text):
        raise ValueError("free text must be printable ASCII")
    if any(ch.isdigit() for ch in MONTH_TEXT.sub("", PLACEHOLDER.sub("", text))):
        raise ValueError("free text carries a figure")
    return text


class PayloadFact(BaseModel):
    model_config = _STRICT
    placeholder: str | None = Field(default=None, pattern=PLACEHOLDER.pattern)
    kind: Kind
    field: FieldName
    size: Size | None = None
    month: str | None = Field(default=None, pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")
    category: str | None = None  # a generic Category name, sent as is

    @field_validator("category")
    @classmethod
    def _generic(cls, v: str | None) -> str | None:
        if v is not None and v not in GENERIC_CATEGORIES:
            raise ValueError("only generic Category names leave")
        return v


class GatewayPayload(BaseModel):
    """Everything a cloud model may see. Build it with `redact`."""

    model_config = _STRICT
    intent: str = Field(pattern=r"^[a-z][a-z_]{0,39}$")
    question: str = Field(max_length=MAX_QUESTION)
    facts: tuple[PayloadFact, ...] = ()
    notes: tuple[str, ...] = ()

    @field_validator("question")
    @classmethod
    def _question(cls, v: str) -> str:
        return _check_free_text(v)

    @field_validator("notes")
    @classmethod
    def _notes(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for note in v:
            if len(note) > MAX_NOTE:
                raise ValueError("note too long")
            _check_free_text(note)
        return v


# ---- what stays -------------------------------------------------------------


class PlaceholderMap(BaseModel):
    """Placeholder -> real value, for the local fill only. It never serialises."""

    model_config = _STRICT
    entries: tuple[tuple[str, Value], ...] = ()

    def __getitem__(self, placeholder: str) -> Value:
        for key, value in self.entries:
            if key == placeholder:
                return value
        raise KeyError("unknown placeholder")

    def __contains__(self, placeholder: object) -> bool:
        return any(key == placeholder for key, _ in self.entries)

    def placeholders(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.entries)

    @model_serializer
    def _refuse(self) -> dict[str, Any]:
        raise TypeError("PlaceholderMap holds real values and never serialises")

    def __iter__(self) -> NoReturn:  # type: ignore[override]  # dict(m) would copy it out
        raise TypeError("PlaceholderMap is not iterable")

    def __getstate__(self) -> NoReturn:
        raise TypeError("PlaceholderMap never pickles")

    def __repr_args__(self) -> list[tuple[str | None, Any]]:
        return [("placeholders", len(self.entries))]

    def __repr__(self) -> str:
        return f"PlaceholderMap(placeholders={len(self.entries)})"

    __str__ = __repr__


class CloudReply(BaseModel):
    """Cloud wording that passed `validate`: placeholders, words, no figures."""

    model_config = _STRICT
    text: str


type Reason = Literal[
    "empty",
    "too_long",
    "hidden_text",
    "unknown_placeholder",
    "figure",
    "merchant",
    "instruction",
]


class Rejected(BaseModel):
    """Why a reply was refused: a reason only, so it is safe to log."""

    model_config = _STRICT
    reason: Reason
