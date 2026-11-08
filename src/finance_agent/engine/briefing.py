"""The weekly briefing and its regret review (#152, docs/AI_DESIGN.md): computed
by code, no model involved, shown in the app only and never sent anywhere.

A week starts on its Monday (`week_of`).

Regret review: each week, `PICKS` discretionary Transactions to ask "worth
it?" about. The rule, so a week's list never changes under the user:

- candidates are Spending (never Income, a Transfer, a Refund or a Fee)
  posted in the `LOOKBACK_DAYS` before the week starts, from a Merchant with
  no recurring schedule and not in the Subscriptions registry (those are not
  discretionary), and not answered in another week;
- each is keyed by SHA-256 of "<user id>:<week's Monday>:<Transaction id>",
  the seed being the user and the week, and the `PICKS` lowest keys are taken.

So the pick depends only on the set of rows, not their order, and answering an
item this week leaves the week's list as it was. The answers are feedback
(`ledger.regret`), never Memories.
"""

import hashlib
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import Connection

from finance_agent.categorise.recurring import detect_recurring
from finance_agent.engine import forecast, insights
from finance_agent.engine.detectors import LEAD_DAYS
from finance_agent.ledger import Ledger, Transaction
from finance_agent.ledger.regret import Regret

PICKS = 3
LOOKBACK_DAYS = 28  # four weeks, so a skipped week's spending still comes up
TOP_INSIGHTS = 3

Part = Literal["insights", "forecast"]


def week_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True)
class MonthBand:
    """The forecast of the week's month, in pence of money out: the
    `engine.forecast.cash_flow` figures, unchanged."""

    month: str  # YYYY-MM
    point: int
    lower: int
    upper: int
    recurring: int  # the scheduled part
    left_out: int  # Categories without enough history, not in the band


@dataclass(frozen=True)
class Renewal:
    insight_id: int
    merchant: str
    due_on: date
    amount: int  # pence, the last charge


@dataclass(frozen=True)
class Briefing:
    week: date  # its Monday
    insights: list[insights.Ranked]  # the top of the Insight feed
    forecast: MonthBand | None  # None: not enough history
    next_payday: date | None  # from the recurring income's schedule
    renewals: list[Renewal]  # due from the week on, soonest first
    regret: list[Transaction]  # this week's "worth it?" items
    answered: dict[int, bool]  # those of them already answered
    insufficient: tuple[Part, ...]  # parts with too little history to show


def build(conn: Connection, user_id: int, day: date) -> Briefing:
    """The briefing for `day`'s week. It refreshes the stored Insights as
    `GET /api/insights` does (idempotent) and logs its shown Insights in the
    exposure log (#261); nothing else is written."""
    week = week_of(day)
    led = Ledger(conn, user_id)
    txns = led.transactions()
    insights.refresh(conn, user_id)
    feed = insights.ranked(conn, user_id)
    renewals = sorted(
        (
            Renewal(i.id, i.key, i.raised_on + timedelta(days=LEAD_DAYS), i.impact)
            for r in feed
            for i in (r.insight,)
            if i.type == "upcoming_renewal"
            and i.raised_on + timedelta(days=LEAD_DAYS) >= week
        ),
        key=lambda r: (r.due_on, r.insight_id),
    )
    flow = forecast.cash_flow(led, week, 1)
    (month,) = flow.months
    left_out = sum(not f.months for f in flow.categories.values())
    band = (
        MonthBand(
            month.month,
            month.total.point,
            month.total.lower,
            month.total.upper,
            month.recurring,
            left_out,
        )
        if left_out < len(flow.categories)  # some Category could be forecast
        else None
    )
    paydays = [d for p in flow.income for d in p.dates if d >= week]
    answers = Regret(conn, user_id).answers()
    regret = pick(
        user_id,
        week,
        txns,
        {i: a.week for i, a in answers.items()},
        _not_discretionary(led, txns, week),
    )
    missing: list[Part] = []
    if not insights.rows(led):
        missing.append("insights")
    if band is None:
        missing.append("forecast")
    return Briefing(
        week,
        insights.shown(conn, user_id, TOP_INSIGHTS, day),
        band,
        min(paydays, default=None),
        renewals,
        regret,
        {t.id: answers[t.id].worth_it for t in regret if t.id in answers},
        tuple(missing),
    )


def _not_discretionary(
    led: Ledger, txns: Iterable[Transaction], week: date
) -> set[int]:
    """Merchant ids in the Subscriptions registry or with a recurring schedule
    in the Spending before the week."""
    charges: dict[int, list[tuple[date, int]]] = {}
    for t in txns:
        c = t.categorisation
        if c and c.kind == "Spending" and c.merchant_id and t.posted_on < week:
            charges.setdefault(c.merchant_id, []).append(
                (t.posted_on, int(-t.amount.scaleb(2)))
            )
    scheduled = {m for m, cs in charges.items() if detect_recurring(sorted(cs))}
    return scheduled | {e.merchant_id for e in led.subscriptions()}


def _key(user_id: int, week: date, transaction_id: int) -> str:
    seed = f"{user_id}:{week.isoformat()}:{transaction_id}"
    return hashlib.sha256(seed.encode()).hexdigest()


def pick(
    user_id: int,
    week: date,
    txns: Iterable[Transaction],
    answered: Mapping[int, date],
    not_discretionary: Collection[int],
) -> list[Transaction]:
    """This week's regret-review items, by the rule above. `answered` maps a
    Transaction id to the week it was answered in; `not_discretionary` holds
    Merchant ids with a schedule or a registry entry."""
    since = week - timedelta(days=LOOKBACK_DAYS)
    candidates = [
        t
        for t in txns
        if (c := t.categorisation) is not None
        and c.kind == "Spending"
        and t.transfer_with is None
        and c.merchant_id not in not_discretionary
        and since <= t.posted_on < week
        and answered.get(t.id, week) == week
    ]
    candidates.sort(key=lambda t: _key(user_id, week, t.id))
    return candidates[:PICKS]
