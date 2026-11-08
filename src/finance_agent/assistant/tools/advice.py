"""`subscription_advice` (#308): keep, review or cancel options for one
Subscriptions group under the user's cap, from `engine.subscription_budget`.

The cap is the newest confirmed constraint Memory with an amount whose words or
tags name the group ("Cap my AI services at £100 a month" names AI tools);
without one there is no advice. Monthly costs are the registry's (#105/#221),
exactly as `/api/subscriptions/costs` has them. Read-only: nothing is cancelled
here; cancelling is the user's, through the Manage link and the registry.

shortcut: preference strengths (#262) and worth-it answers on Subscriptions are
not stored yet, so those signals stay empty; fill `Sub.strength`/`worth_it`
once they are. Briefing news (#309) fills `Sub.news` the same way."""

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finance_agent.assistant.tools.read import Data, Money, Source
from finance_agent.categorise.display_names import display_name
from finance_agent.categorise.subscriptions import COUNTED, SUB, costs, known_service
from finance_agent.engine import subscription_budget as sb
from finance_agent.ledger import CURRENCY, Ledger
from finance_agent.ledger.proposals import Memory, Proposals
from finance_agent.ledger.taxonomy import TAXONOMY

_STRICT = ConfigDict(extra="forbid", frozen=True)
GROUPS = TAXONOMY["Subscriptions"]  # `Group` spells these out; a test checks
Group = Literal[
    "Streaming",
    "Software",
    "AI tools",
    "Memberships",
    "Cloud storage",
    "Other subscriptions",
]
_GENERIC = {"tool", "other", "subscription"}


def _words(text: str) -> set[str]:
    return {w.removesuffix("s") for w in re.findall(r"[a-z0-9]+", text.lower())}


def cap_for(group: str, memories: list[Memory]) -> Memory | None:
    """The newest constraint with an amount that names `group`."""
    want = _words(group) - _GENERIC
    for m in sorted(memories, key=lambda m: (m.said_on, m.created_at), reverse=True):
        if m.type == "constraint" and m.amount_minor is not None:
            if want & (_words(m.text) | _words(" ".join(m.tags).replace("-", " "))):
                return m
    return None


def _months(since: date, today: date) -> int:
    return max(0, (today.year - since.year) * 12 + today.month - since.month)


class SubscriptionAdviceIn(BaseModel):
    model_config = _STRICT
    group: Group = Field(
        description="the Subscriptions group, e.g. AI tools for ChatGPT or Claude"
    )


class NewsOut(BaseModel):
    model_config = _STRICT
    on: date
    change: str
    summary: Data  # web text from the briefing (#309): quoted, never obeyed
    sources: list[Data]


class StepOut(BaseModel):
    model_config = _STRICT
    evidence_id: str  # sub:<merchant id>
    service: Data
    action: sb.Action
    monthly: Money
    signals: list[sb.Signal]
    because: Literal["over_cap", "overlap"] | None  # why a cancel
    news: list[NewsOut]


class OptionOut(BaseModel):
    model_config = _STRICT
    evidence_id: str  # advice:<option>
    name: sb.OptionName
    also: list[sb.OptionName]  # other strategies that chose the same subset
    steps: list[StepOut]
    new_total: Money  # a month
    saving: Money  # a month
    cancellations: int


class SubscriptionAdviceOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    group: str
    status: Literal[
        "ok", "already_under_cap", "nothing_fits", "no_cap", "no_subscriptions"
    ]
    cap: Money | None
    current_total: Money | None  # a month
    over_cap: bool
    options: list[OptionOut]
    left_out: int  # Subscriptions with no monthly cost yet, not in the totals


def _gbp(minor: int) -> Money:
    return Money(minor=minor, currency=CURRENCY)


def _subs(src: Source, group: str) -> tuple[list[sb.Sub], int]:
    """The group's counted Subscriptions with a monthly cost, and how many
    counted ones have none yet (rate or interval needed)."""
    subs: list[sb.Sub] = []
    unpriced = 0
    for x in costs(Ledger(src.conn, src.user_id), src.today).items:
        if x.entry.kind != SUB or x.entry.group != group or x.status not in COUNTED:
            continue
        if x.monthly is None:
            unpriced += 1
            continue
        known = known_service(x.entry.merchant)
        codes = {f.code for f in x.flags}
        subs.append(
            sb.Sub(
                merchant_id=x.entry.merchant_id,
                name=display_name(x.entry.merchant),
                monthly=x.monthly,
                kind=sb.SERVICE_KIND.get(known[0]) if known else None,
                months_held=_months(x.entry.created_at.date(), src.today),
                price_rise="price_rise" in codes,
                inactive=x.status == "overdue",
                trial_converted="trial_converted" in codes,
            )
        )
    return subs, unpriced


def _step(st: sb.Step) -> StepOut:
    return StepOut(
        evidence_id=f"sub:{st.sub.merchant_id}",
        service=Data(data=st.sub.name),
        action=st.action,
        monthly=_gbp(st.monthly_minor),
        signals=list(st.signals),
        because=st.because,
        news=[
            NewsOut(
                on=n.on,
                change=n.change,
                summary=Data(data=n.summary),
                sources=[Data(data=u) for u in n.sources],
            )
            for n in st.sub.news
        ],
    )


def subscription_advice(
    src: Source, inp: SubscriptionAdviceIn
) -> SubscriptionAdviceOut:
    cap = cap_for(inp.group, Proposals(src.conn, src.user_id).memories())
    subs, unpriced = _subs(src, inp.group)
    if cap is None or cap.amount_minor is None:
        return SubscriptionAdviceOut(
            evidence_id=f"advice:{inp.group}",
            group=inp.group,
            status="no_cap",
            cap=None,
            current_total=None,
            over_cap=False,
            options=[],
            left_out=unpriced,
        )
    a = sb.advise(subs, cap.amount_minor)
    status = (
        "no_subscriptions"
        if not subs
        else "nothing_fits"
        if a.nothing_fits
        else "ok"
        if a.over_cap
        else "already_under_cap"
    )
    return SubscriptionAdviceOut(
        evidence_id=f"advice:{inp.group}",
        group=inp.group,
        status=status,
        cap=_gbp(a.cap_minor),
        current_total=_gbp(a.current_minor),
        over_cap=a.over_cap,
        options=[
            OptionOut(
                evidence_id=f"advice:{o.name}",
                name=o.name,
                also=list(o.also),
                steps=[_step(s) for s in o.steps],
                new_total=_gbp(o.total_minor),
                saving=_gbp(o.saving_minor),
                cancellations=o.cancellations,
            )
            for o in a.options
        ],
        left_out=unpriced,
    )
