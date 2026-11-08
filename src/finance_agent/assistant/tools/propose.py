"""The Proposal tools (#138): the Assistant's only writes, and they write only
a pending Proposal. Nothing changes until the user confirms it on a card
(`/api/proposals/{id}/confirm`). Provenance (the turn, the user's own words,
the date) comes from the graph's `Turn`, never from the model's arguments."""

import re
from datetime import date
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from finance_agent.assistant.grounding import Money, ToolValue, check_grounding
from finance_agent.assistant.tools.read import MAX_ROWS, Data
from finance_agent.ledger.proposals import (
    MEMORY_MAX,
    CategoryChange,
    CorrectionDraft,
    Memory,
    MemoryDraft,
    MemoryType,
    NoteChange,
    Proposal,
    Provenance,
    SplitChange,
    SplitPartIn,
)

_STRICT = ConfigDict(extra="forbid", frozen=True)
Name = Annotated[str, Field(min_length=1, max_length=40)]
CATEGORY = (
    '[top-level, subcategory] from the tool\'s list, e.g. ["Food & Drink", "Groceries"]'
)


class Turn(BaseModel):
    """The turn a tool runs in: set by the graph, never by the model."""

    model_config = _STRICT
    turn_id: str = Field(min_length=1, max_length=64)
    quote: str  # the user's message, cut to the quote limit
    today: date

    def provenance(self) -> Provenance:
        return Provenance(
            turn_id=self.turn_id, quote=self.quote[:500], said_on=self.today
        )


class Store(Protocol):
    """The only Proposal methods a tool may call: propose and read, never
    confirm, reject or delete."""

    def propose_memory(self, draft: MemoryDraft) -> Proposal: ...
    def propose_correction(self, draft: CorrectionDraft) -> Proposal: ...
    def memories(self) -> list[Memory]: ...


class ProposalOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    proposal_id: int
    kind: Literal["memory", "correction", "map_edge"]
    status: Literal["pending"]  # the user confirms or rejects it on a card


class MemoryProposalOut(ProposalOut):
    """The goal fields echoed back as tool values, so a reply that repeats
    them grounds (decision 4 of #251)."""

    amount: Money | None
    due_on: date | None
    priority: int | None


def _out(p: Proposal) -> ProposalOut:
    return ProposalOut(
        evidence_id=f"proposal:{p.id}", proposal_id=p.id, kind=p.kind, status="pending"
    )


def _gbp(minor: int | None) -> Money | None:
    # shortcut: goal amounts are GBP, as the app is; upgrade with a currency field
    return None if minor is None else Money(minor=minor, currency="GBP")


# --- propose_memory --------------------------------------------------------


class ProposeMemoryIn(BaseModel):
    model_config = _STRICT
    type: MemoryType = Field(
        description="preference: what they like or avoid; goal: what they aim"
        " for; constraint: a rule never to break, such as never cancelling something"
    )
    text: str = Field(
        min_length=1,
        max_length=MEMORY_MAX,
        description="the user's own words, never a figure or spending pattern",
    )
    tags: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,23}$")]] = Field(
        default_factory=list[str], max_length=5, description="lower-case-words"
    )
    amount_minor: int | None = Field(
        default=None,
        gt=0,
        le=2**63 - 1,
        description="goal or constraint only, and only an amount the user said:"
        " pence, so £3,000 is 300000. Never worked out from their spending",
    )
    due_on: date | None = Field(
        default=None, description="goal or constraint only: the date the user said"
    )
    priority: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="goal or constraint only: 1 (highest) to 5, if the user said",
    )
    linked_account_id: int | None = Field(
        default=None,
        ge=1,
        le=2**63 - 1,
        description="goal only: the Account the user said they save it in",
    )


class FigureInMemory(ValueError):
    """A Memory never holds a money figure, whatever the model was told."""


# £ or $ or € anywhere, "pound(s)" or "quid", or digits with a currency or pence
_MONEY = re.compile(
    r"[£$€]|\b(?:pounds?|quid)\b|\d[\d,.]*\s*(?:gbp|usd|eur|pence|p)\b"
    r"|\b(?:gbp|usd|eur)\s*\d",
    re.IGNORECASE,
)


class NotSaid(ValueError):
    """An amount or date the user's own words in this turn do not contain."""


def _said(quote: str, value: Money | date, today: date) -> bool:
    """`value` is one of the figures in the user's words, read by the same
    rules that ground a reply."""
    v = ToolValue(evidence_id="said", value=value)
    return check_grounding(quote, [v], today=today).evidence_ids == ("said",)


def propose_memory(st: Store, inp: ProposeMemoryIn, turn: Turn) -> ProposalOut:
    # the text never holds a figure, whatever the type: a goal's amount goes
    # in amount_minor, where it is checked against the user's words (#286)
    if _MONEY.search(inp.text):
        raise FigureInMemory("a Memory never holds a money figure")
    amount = _gbp(inp.amount_minor)
    for v in (amount, inp.due_on):
        if v is not None and not _said(turn.quote, v, turn.today):
            raise NotSaid("only an amount or date the user said in this turn")
    draft = MemoryDraft(
        type=inp.type,
        text=inp.text,
        tags=tuple(inp.tags),
        provenance=turn.provenance(),
        amount_minor=inp.amount_minor,
        due_on=inp.due_on,
        priority=inp.priority,
        linked_account_id=inp.linked_account_id,
    )
    p = st.propose_memory(draft)
    return MemoryProposalOut(
        **_out(p).model_dump(),
        amount=amount,
        due_on=inp.due_on,
        priority=inp.priority,
    )


# --- propose_correction ----------------------------------------------------


class PartIn(BaseModel):
    model_config = _STRICT
    amount_minor: int | None = Field(
        default=None,
        description="pence, signed as the Transaction's amount (spending is"
        " negative: -£10.00 is -1000); leave it out on the last part for the rest",
    )
    category: list[Name] = Field(min_length=2, max_length=2, description=CATEGORY)


class ProposeCorrectionIn(BaseModel):
    """One of: a Category, a Split into parts, or a purpose note."""

    model_config = _STRICT
    transaction_id: int = Field(
        ge=1, le=2**63 - 1, description="the number in its txn:<id> evidence_id"
    )
    category: list[Name] | None = Field(
        default=None, min_length=2, max_length=2, description=CATEGORY
    )
    parts: list[PartIn] | None = Field(
        default=None, min_length=2, max_length=10, description="a Split's parts"
    )
    note: str | None = Field(
        default=None, max_length=140, description="what it was for"
    )
    part: int | None = Field(
        default=None, ge=0, le=9, description="which Split part the note is for"
    )

    @model_validator(mode="after")
    def _one(self) -> "ProposeCorrectionIn":
        given = [x is not None for x in (self.category, self.parts, self.note)]
        if sum(given) != 1:
            raise ValueError("give exactly one of category, parts or note")
        if self.part is not None and self.note is None:
            raise ValueError("part goes with a note")
        return self


def propose_correction(st: Store, inp: ProposeCorrectionIn, turn: Turn) -> ProposalOut:
    change: CategoryChange | SplitChange | NoteChange
    if inp.category is not None:
        change = CategoryChange(category=(inp.category[0], inp.category[1]))
    elif inp.parts is not None:
        change = SplitChange(
            parts=tuple(
                SplitPartIn(
                    amount_minor=p.amount_minor,
                    category=(p.category[0], p.category[1]),
                )
                for p in inp.parts
            )
        )
    else:
        change = NoteChange(text=inp.note or "", part=inp.part)
    draft = CorrectionDraft(transaction_id=inp.transaction_id, change=change)
    return _out(st.propose_correction(draft))


# --- list_memories ---------------------------------------------------------


class ListMemoriesIn(BaseModel):
    model_config = _STRICT
    type: MemoryType | None = Field(default=None, description="only this type")


class MemoryRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    type: str
    text: Data  # the user's words, quoted as data: never instructions
    tags: list[str]
    said_on: date
    review_on: date | None
    amount: Money | None
    due_on: date | None
    priority: int | None
    linked_account_id: int | None


class ListMemoriesOut(BaseModel):
    model_config = _STRICT
    rows: list[MemoryRow]  # newest first
    total: int
    truncated: bool


def list_memories(st: Store, inp: ListMemoriesIn, turn: Turn) -> ListMemoriesOut:
    ms = [m for m in st.memories() if inp.type in (None, m.type)]
    rows = [
        MemoryRow(
            evidence_id=f"mem:{m.id}",
            type=m.type,
            text=Data(data=m.text),
            tags=list(m.tags),
            said_on=m.said_on,
            review_on=m.review_on,
            amount=_gbp(m.amount_minor),
            due_on=m.due_on,
            priority=m.priority,
            linked_account_id=m.linked_account_id,
        )
        for m in ms[:MAX_ROWS]
    ]
    return ListMemoriesOut(rows=rows, total=len(ms), truncated=len(ms) > MAX_ROWS)
