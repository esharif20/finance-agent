"""Subscription alerts (#108): a notification for a price rise, a charge after
the user cancelled, and a free trial converting within 2 days. Each fires once
per state: `subscription_alert` holds what was sent, keyed by Merchant, code
and state (the flag's evidence date, or `converts_on` for a trial), written
insert-if-absent. A body names the Merchant and the event, never an amount.

`run` is called after `fa import`, `fa sync`, `POST /api/sync` and a watched
folder's import, with the Notifier each injects. It never raises: alerts must
not fail an import or a sync. Logs carry counts only.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.sqlite import insert

from finance_agent.categorise import subscriptions
from finance_agent.categorise.subscriptions import CostSummary
from finance_agent.ledger import Ledger
from finance_agent.notify import TITLE, Notifier
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

LONDON = ZoneInfo("Europe/London")
TRIAL_NOTICE_DAYS = 2  # a trial converting within this many days is announced
NO_ALERT = ("cancelled", "lapsed")  # a trial the user left needs no warning


@dataclass(frozen=True)
class Alert:
    merchant_id: int
    merchant: str
    code: str  # price_rise, charged_after_cancel or trial_converting
    state: date


def due(summary: CostSummary, today: date) -> list[Alert]:
    """Every alert the cost summary supports today, sent or not."""
    out: list[Alert] = []
    for x in summary.items:
        e = x.entry
        for f in x.flags:
            if f.code in ("price_rise", "charged_after_cancel") and f.on:
                out.append(Alert(e.merchant_id, e.merchant, f.code, f.on))
        t = x.trial
        if (
            t is not None
            and not t.converted
            and x.status not in NO_ALERT
            and today <= t.converts_on <= today + timedelta(TRIAL_NOTICE_DAYS)
        ):
            out.append(
                Alert(e.merchant_id, e.merchant, "trial_converting", t.converts_on)
            )
    return out


def message(a: Alert) -> str:
    if a.code == "price_rise":
        return f"{a.merchant}: the price went up"
    if a.code == "charged_after_cancel":
        return f"{a.merchant}: charged after you cancelled it"
    return f"{a.merchant}: free trial becomes paid on {a.state:%-d %b}"


def _claim(conn: Connection, user_id: int, a: Alert) -> bool:
    """Record the alert unless already sent; True when this call recorded it."""
    res = conn.execute(
        insert(schema.subscription_alert)
        .values(
            user_id=user_id,
            merchant_id=a.merchant_id,
            code=a.code,
            state=a.state,
            alerted_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing()
    )
    return res.rowcount == 1


def run(
    engine: Engine, user_id: int, notify: Notifier, today: date | None = None
) -> int:
    """Send each due alert not sent before; returns how many were sent. A
    notification is sent inside the transaction that records it, so one that
    fails is rolled back and tried again on the next run."""
    today = today or datetime.now(LONDON).date()
    sent = 0
    try:
        with engine.begin() as conn:
            summary = subscriptions.costs(Ledger(conn, user_id), today)
            for a in due(summary, today):
                if _claim(conn, user_id, a):
                    notify(TITLE, message(a))
                    sent += 1
    except Exception as e:  # noqa: BLE001
        log.warning("subscription alerts failed: %s", type(e).__name__)
        return 0
    log.info("subscription alerts sent=%d", sent)
    return sent


def forget(conn: Connection, user_id: int) -> int:
    """Delete this user's record of alerts sent (`fa forget-alerts`); an alert
    still due is then sent again. Returns how many rows went."""
    db = Scoped(conn, user_id)
    n = db.execute(db.delete(schema.subscription_alert)).rowcount
    log.info("subscription alerts forgotten=%d", n)
    return n
