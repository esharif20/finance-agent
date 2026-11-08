"""The weekly briefing (#152): this week's top Insights, forecast band,
upcoming renewals and regret review, for `current_user` only. Computed by
code (`engine.briefing`), shown in the app, never sent anywhere. Regret answers
are feedback, never Memories. Nothing here logs."""

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path as FilePath
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import Connection

from finance_agent.api.fx import Money
from finance_agent.api.insights import DeletedOut, InsightOut, insight_out
from finance_agent.categorise.display_names import display_name
from finance_agent.engine import briefing, topics
from finance_agent.gateway.types import TOPIC
from finance_agent.identity import current_user
from finance_agent.ledger import Ledger
from finance_agent.ledger.regret import Regret
from finance_agent.storage.briefing import BriefingStore

SQLITE_MAX = 2**63 - 1
TransactionId = Annotated[int, Path(ge=1, le=SQLITE_MAX)]
# the day whose week to brief; today when left out
On = Annotated[date | None, Query()]


class BandOut(BaseModel):
    month: str  # YYYY-MM
    point_minor: int  # pence of money out, from engine.forecast
    lower_minor: int
    upper_minor: int
    recurring_minor: int
    left_out: int  # Categories with too little history, not in the band


class RenewalOut(BaseModel):
    insight_id: int
    merchant: str
    display_name: str
    due_on: date
    amount_minor: int  # pence, the last charge


class RegretItemOut(BaseModel):
    transaction_id: int
    posted_on: date
    description: str
    merchant: str | None
    display_name: str | None
    amount: Money
    worth_it: bool | None  # None until answered


class BriefingOut(BaseModel):
    week: date  # its Monday
    insights: list[InsightOut]
    forecast: BandOut | None  # None: not enough history (see `insufficient`)
    next_payday: date | None
    renewals: list[RenewalOut]
    regret: list[RegretItemOut]
    insufficient: list[str]  # "insights", "forecast": too little history yet


class RegretAnswerIn(BaseModel):
    worth_it: bool = Field(strict=True)


class RegretAnswerOut(BaseModel):
    transaction_id: int
    worth_it: bool


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
router = APIRouter(prefix="/api/briefing")


@router.get("")
def weekly(conn: Conn, on: On = None) -> BriefingOut:
    """The briefing for the week holding `on` (today by default)."""
    b = briefing.build(conn, current_user(), on or date.today())
    f = b.forecast
    return BriefingOut(
        week=b.week,
        insights=[insight_out(r) for r in b.insights],
        forecast=None
        if f is None
        else BandOut(
            month=f.month,
            point_minor=f.point,
            lower_minor=f.lower,
            upper_minor=f.upper,
            recurring_minor=f.recurring,
            left_out=f.left_out,
        ),
        next_payday=b.next_payday,
        renewals=[
            RenewalOut(
                insight_id=r.insight_id,
                merchant=r.merchant,
                display_name=display_name(r.merchant),
                due_on=r.due_on,
                amount_minor=r.amount,
            )
            for r in b.renewals
        ],
        regret=[
            RegretItemOut(
                transaction_id=t.id,
                posted_on=t.posted_on,
                description=t.description,
                merchant=t.categorisation.merchant if t.categorisation else None,
                display_name=(
                    display_name(t.categorisation.merchant)
                    if t.categorisation and t.categorisation.merchant
                    else None
                ),
                amount=Money(minor=int(t.amount.scaleb(2)), currency=t.currency),
                worth_it=b.answered.get(t.id),
            )
            for t in b.regret
        ],
        insufficient=list(b.insufficient),
    )


@router.post("/regret/{transaction_id}")
def answer(
    conn: Conn, transaction_id: TransactionId, body: RegretAnswerIn, on: On = None
) -> RegretAnswerOut:
    """Answer "worth it?" on one of the week's regret items; a later answer
    replaces it. Anything else, including another user's, is not found."""
    user = current_user()
    b = briefing.build(conn, user, on or date.today())
    if transaction_id not in {t.id for t in b.regret}:
        raise HTTPException(404, "not one of this week's regret items")
    Regret(conn, user).answer(transaction_id, b.week, body.worth_it)
    return RegretAnswerOut(transaction_id=transaction_id, worth_it=body.worth_it)


@router.delete("/regret")
def forget_answers(conn: Conn) -> DeletedOut:
    """Wipe every regret answer."""
    return DeletedOut(deleted=Regret(conn, current_user()).forget())


# ---- the daily news briefing (#309) -----------------------------------------
# Topics come from spending (`engine.topics`); only the ones ticked here leave,
# as names, from `fa brief refresh`. Items are public web text: shown with
# their sources and dates as plain text, never acted on. `spent` is the user's
# own figure for the "relevant to your spending" line, filled in here.

TopicName = Annotated[str, Field(pattern=TOPIC)]


class TopicOut(BaseModel):
    name: str
    state: Literal["on", "off", "suggested"]  # only "on" leaves the Mac
    from_spending: bool
    spent: Money | None  # over the last `window_months`; never sent


class NewsSourceOut(BaseModel):
    url: str  # https only (gateway.brief)
    title: str


class NewsItemOut(BaseModel):
    id: int
    headline: str
    summary: str
    change: str
    on: date
    sources: list[NewsSourceOut]
    read: bool


class NewsCardOut(BaseModel):
    topic: str
    spent: Money | None
    items: list[NewsItemOut]


class NewsOut(BaseModel):
    available: bool  # False: no ledger file for briefing.sqlite to sit beside
    window_months: int
    topics: list[TopicOut]
    cards: list[NewsCardOut]


class TopicIn(BaseModel):
    name: TopicName
    state: Literal["on", "off"]


def _store(request: Request) -> BriefingStore | None:
    db = getattr(getattr(request.app.state.engine, "url", None), "database", None)
    if os.environ.get("FA_DEMO") or not db or db == ":memory:":
        return None
    return BriefingStore.beside(FilePath(db))


Store = Annotated[BriefingStore | None, Depends(_store)]


def _gbp(minor: int) -> Money:
    return Money(minor=minor, currency="GBP")


@router.get("/news")
def news(conn: Conn, store: Store, on: On = None) -> NewsOut:
    """The topic list (suggested from spending, ticked, blocked or added) and
    a card per topic with its fresh items, newest first."""
    user, today = current_user(), on or date.today()
    suggested = topics.for_ledger(Ledger(conn, user), today)
    listed = topics.listing(suggested, store.topics(user) if store else {})
    spent = {n.lower(): _gbp(t.spent_minor) for n, _, t in listed if t}
    cards: dict[str, NewsCardOut] = {}
    for it in store.items(user, today) if store else []:
        card = cards.setdefault(
            it.topic.lower(),
            NewsCardOut(topic=it.topic, spent=spent.get(it.topic.lower()), items=[]),
        )
        card.items.append(
            NewsItemOut(
                id=it.id,
                headline=it.headline,
                summary=it.summary,
                change=it.change,
                on=it.on,
                sources=[NewsSourceOut(url=s.url, title=s.title) for s in it.sources],
                read=it.read,
            )
        )
    return NewsOut(
        available=store is not None,
        window_months=topics.MONTHS,
        topics=[
            TopicOut(
                name=n,
                state=state,
                from_spending=t is not None,
                spent=_gbp(t.spent_minor) if t else None,
            )
            for n, state, t in listed
        ],
        cards=list(cards.values()),
    )


def _need(store: BriefingStore | None) -> BriefingStore:
    if store is None:
        raise HTTPException(409, "the briefing needs the local ledger")
    return store


@router.put("/topics")
def set_topic(body: TopicIn, store: Store) -> TopicOut:
    """Tick (`on`), block or mute (`off`) a topic, or add one of your own."""
    _need(store).set_topic(current_user(), body.name, body.state)
    return TopicOut(name=body.name, state=body.state, from_spending=False, spent=None)


@router.delete("/topics/{name}")
def forget_topic(name: Annotated[str, Path(pattern=TOPIC)], store: Store) -> DeletedOut:
    """Forget a tick or block: a spending topic goes back to suggested."""
    return DeletedOut(deleted=int(_need(store).forget_topic(current_user(), name)))


@router.post("/news/{item_id}/read", status_code=204)
def read_item(item_id: TransactionId, store: Store) -> None:
    """Mark an item read; another user's or an unknown one is not found."""
    if not _need(store).mark_read(current_user(), item_id):
        raise HTTPException(404, "no such item")
