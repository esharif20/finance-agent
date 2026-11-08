"""`fa brief refresh`: the daily briefing's one cloud call (#309; ADR 0016).

Only the topics the user ticked leave, as names mixed with decoys
(`gateway.brief.payload`), to the pinned Gemini host with Search grounding.
The call is metered in the `briefing` bucket and its payload is logged in
`spend.sqlite` beside the gateway's, so the user can see exactly what went.
`fa brief install` runs it daily from launchd. Nothing printed names a topic,
an item or a figure."""

import json
import random
from datetime import date
from pathlib import Path

from sqlalchemy import Engine

from finance_agent.cli import watch
from finance_agent.engine import topics
from finance_agent.gateway import brief
from finance_agent.gateway.cloud import (
    FLASH_MODEL,
    CloudChatModel,
    CloudError,
    Usage,
    brief_request_body,
)
from finance_agent.gateway.cost import Denied
from finance_agent.ledger import Ledger
from finance_agent.storage.briefing import BriefingStore
from finance_agent.storage.spend import SpendStore

LABEL = "com.finance-agent.brief"
BUCKET = "briefing"
MAX_TOKENS = 4096  # thinking counts too
THREAD = "briefing"  # the payload log's conversation id for these calls
DAILY: dict[str, object] = {"StartCalendarInterval": {"Hour": 7, "Minute": 30}}


def refresh(
    user_id: int,
    engine: Engine,
    ledger_path: Path,
    cloud: CloudChatModel,
    today: date,
    rng: random.Random | None = None,
) -> int:
    with engine.connect() as conn:
        suggested = topics.for_ledger(Ledger(conn, user_id), today)
    store = BriefingStore.beside(ledger_path)
    send = topics.to_send(suggested, store.topics(user_id))
    if not send:
        print("Briefing: no topics ticked yet; tick some on the Briefing screen")
        return 0
    try:
        p = brief.payload(send, rng)
    except ValueError:
        print("fa brief: a topic is not words only; nothing was sent")
        return 1
    meter = SpendStore.beside(ledger_path)
    max_in = len(json.dumps(brief_request_body(p, MAX_TOKENS)))
    d = meter.reserve(BUCKET, FLASH_MODEL, max_in, MAX_TOKENS)
    if isinstance(d, Denied):
        print("fa brief: the briefing's monthly cloud cap is reached; nothing sent")
        return 1
    failure: CloudError | None = None
    try:
        text, usage = cloud.brief(p, model=FLASH_MODEL, max_tokens=MAX_TOKENS)
    except CloudError as e:  # it may have been billed: charge the reservation
        text, usage, failure = "", Usage(max_in, MAX_TOKENS, 0, FLASH_MODEL), e
    pence = meter.settle(d.reservation, usage)
    meter.log_payload(user_id, THREAD, p.model_dump(mode="json"), FLASH_MODEL, pence)
    if failure is not None:
        print(f"fa brief: {failure}")
        return 1
    try:
        items, dropped = brief.parse(text, send, today)
    except brief.BriefError as e:
        print(f"fa brief: {e}")
        return 1
    added = store.add(user_id, items, today)
    print(f"Briefing: {added} new, {dropped} dropped, {pence}p")
    return 0


def install(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> Path:
    return watch.write_agent(LABEL, ["brief", "refresh"], DAILY, agents_dir, ctl)


def remove(agents_dir: Path | None = None, ctl: watch.Launchctl | None = None) -> None:
    watch.remove_agent(LABEL, agents_dir, ctl)
