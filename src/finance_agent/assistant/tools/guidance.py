"""Ask's guidance tools (#259, spec #251): the playbook cards whose conditions
hold for the user, and why one is shown. The Engine evaluates every condition
and fills every slot (engine.playbook, ADR 0007); the tools only carry the
result. Card text is repo data, linted at load, and enters the context as
`{"data": ...}`, so it is quoted, never read for figures: each figure sits in
its own typed field with its evidence ID. `evaluate` is shared with
`GET /api/guidance`."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finance_agent.assistant.tools.read import MAX_ROWS, Money, NoData, Source
from finance_agent.engine import insights, playbook
from finance_agent.ledger import Ledger

_STRICT = ConfigDict(extra="forbid", frozen=True)
Tone = Literal["gentle", "blunt"]
SAVINGS = "savings"  # the Account kind whose balance counts as savings


class Quote(BaseModel):
    """Card prose: quoted for the model, never instructions or figures."""

    model_config = _STRICT
    data: str = Field(max_length=2000)


class Figure(BaseModel):
    model_config = _STRICT
    name: str  # the signal or fact id
    # money and counts ground; a ratio (months of cover) is text, and so cannot
    value: Money | int | str


class WhyRow(BaseModel):
    model_config = _STRICT
    signal: str
    op: str
    threshold: Money | int | str
    actual: Money | int | str  # the Engine's value for the signal


class FactRow(BaseModel):
    model_config = _STRICT
    evidence_id: str  # fact:<id>
    label: Quote
    value: Money | int
    checked_on: date
    may_be_out_of_date: bool


class CardRow(BaseModel):
    model_config = _STRICT
    evidence_id: str  # card:<id>
    topic: str
    title: Quote
    body: Quote
    figures: list[Figure]
    facts: list[FactRow]


def _fig(name: str, v: playbook.Value) -> Money | int | str:
    if playbook.SIGNALS[name] == "money" and isinstance(v, int):
        return Money(minor=v, currency="GBP")
    return v if isinstance(v, int) else str(v)


def shown_as(name: str, v: playbook.Value) -> str:
    """The value as a card shows it: £1,234.56, a count, or months to a decimal."""
    if playbook.SIGNALS[name] == "money" and isinstance(v, int):
        return playbook.money(v)
    return f"{v:,}" if isinstance(v, int) else str(v)


@dataclass(frozen=True)
class Guidance:
    book: playbook.Playbook
    sigs: playbook.Signals
    shown: list[playbook.Shown]  # the cards whose conditions hold, by id


def _savings(led: Ledger) -> int | None:
    """shortcut: savings are GBP Accounts of kind "savings" with a Bank
    balance, else unknown; refine when Accounts get a typed kind."""
    saved = [a for a in led.accounts() if a.kind == SAVINGS and a.currency == "GBP"]
    if not saved or any(a.bank_balance is None for a in saved):
        return None
    return int(sum((a.bank_balance or Decimal(0) for a in saved), Decimal(0)).scaleb(2))


def evaluate(src: Source, tone: Tone = "gentle") -> Guidance:
    """The user's signals and every card that applies."""
    led = Ledger(src.conn, src.user_id)
    sigs = playbook.signals(insights.rows(led), _savings(led), src.today)
    book = playbook.load()
    shown = [
        s
        for c in book.cards
        if (s := playbook.render(c, sigs, book.facts, tone, src.today)) is not None
    ]
    return Guidance(book, sigs, shown)


def clauses(when: playbook.Condition) -> list[playbook.Clause]:
    """shortcut: a nested condition is flattened, losing its all/any shape;
    the cards are one level deep today."""
    if isinstance(when, playbook.Group):
        return [c for p in when.parts for c in clauses(p)]
    return [when]


def why(g: Guidance, card_id: str) -> list[WhyRow]:
    card = next(c for c in g.book.cards if c.id == card_id)
    return [
        WhyRow(
            signal=c.signal,
            op=c.op,
            threshold=_fig(c.signal, c.value),
            actual=_fig(c.signal, v),
        )
        for c in clauses(card.when)
        if (v := g.sigs.get(c.signal)) is not None
    ]


def fact_rows(g: Guidance, s: playbook.Shown) -> list[FactRow]:
    out: list[FactRow] = []
    for f in s.facts:
        raw = g.book.facts[f.id]
        out.append(
            FactRow(
                evidence_id=f"fact:{f.id}",
                label=Quote(data=f.label),
                value=Money(minor=raw.value, currency="GBP")
                if raw.unit == "money"
                else raw.value,
                checked_on=f.checked_on,
                may_be_out_of_date=f.may_be_out_of_date,
            )
        )
    return out


def card_row(g: Guidance, s: playbook.Shown) -> CardRow:
    return CardRow(
        evidence_id=f"card:{s.card_id}",
        topic=s.topic,
        title=Quote(data=s.title),
        body=Quote(data=s.body),
        figures=[
            Figure(name=n, value=_fig(n, v))
            for n, _ in s.figures
            if (v := g.sigs[n]) is not None  # rendered, so never None
        ],
        facts=fact_rows(g, s),
    )


class ListGuidanceIn(BaseModel):
    model_config = _STRICT
    limit: int = Field(default=5, ge=1, le=MAX_ROWS, description="most cards")


class ListGuidanceOut(BaseModel):
    model_config = _STRICT
    rows: list[CardRow]
    total: int
    truncated: bool


def list_guidance(src: Source, inp: ListGuidanceIn) -> ListGuidanceOut:
    g = evaluate(src)
    return ListGuidanceOut(
        rows=[card_row(g, s) for s in g.shown[: inp.limit]],
        total=len(g.shown),
        truncated=len(g.shown) > inp.limit,
    )


class ExplainCardIn(BaseModel):
    model_config = _STRICT
    card_id: str = Field(
        max_length=64,
        pattern=r"^[a-z]+(?:-[a-z]+)*$",
        description="the id in card:<id>",
    )


class ExplainCardOut(BaseModel):
    model_config = _STRICT
    card: CardRow
    principles: list[Quote]
    when: list[WhyRow]  # the condition's signals: its threshold and the Engine's value


def explain_card(src: Source, inp: ExplainCardIn) -> ExplainCardOut:
    g = evaluate(src)
    # a card whose condition is false is never cited, so it is "no such card"
    s = next((s for s in g.shown if s.card_id == inp.card_id), None)
    if s is None:
        raise NoData("that card does not apply to you")
    return ExplainCardOut(
        card=card_row(g, s),
        principles=[Quote(data=p) for p in s.principles],
        when=why(g, s.card_id),
    )
