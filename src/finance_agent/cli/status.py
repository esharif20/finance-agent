"""`fa status`, `fa month` and `fa subs`: terminal views of the Ledger. Figures
go to the user's own terminal only; nothing here logs."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from fractions import Fraction

from sqlalchemy import Engine

from finance_agent.categorise.display_names import display_name
from finance_agent.categorise.subscriptions import costs, display
from finance_agent.cli import gbp, models
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from finance_agent.storage import backup as store
from finance_agent.storage import default_db_path, keychain, spend, sync_schedule

AI_GROUP = ("subscription", "AI tools")
AI_CAP = Decimal(100)  # the user's AI subscriptions cap (#308)
CLOUD_CAP = Decimal(9)  # ADR 0013
BAR = 24
EIGHTHS = " ▏▎▍▌▋▊▉█"


def bar(amount: Decimal, top: Decimal, width: int = BAR) -> str:
    """A unicode bar in eighths of a cell, proportional to amount / top."""
    if top <= 0 or amount <= 0:
        return ""
    n = int(amount * width * 8 / top)
    return "█" * (n // 8) + (EIGHTHS[n % 8] if n % 8 else "")


def _pounds(pence: int) -> Decimal:
    return Decimal(pence) / 100


def _ago(at: datetime | None, now: datetime) -> str:
    if at is None:
        return "never"
    s = int((now - at).total_seconds())
    if s < 3600:
        return f"{max(s // 60, 0)}m ago"
    return f"{s // 3600}h ago" if s < 86400 else f"{s // 86400}d ago"


def loaded_models() -> list[str] | None:
    """Models Ollama holds in memory now; None when it is not running."""
    doc = models.ollama_json("/api/ps", timeout=1)
    return None if doc is None else models.model_names(doc)


@dataclass(frozen=True)
class Snapshot:
    month: str
    last_sync_at: datetime | None
    last_attempt_at: datetime | None
    accounts: int
    money_in: Decimal
    money_out: Decimal
    review: int
    subs_monthly: Decimal
    ai_monthly: Decimal


def snapshot(engine: Engine, user_id: int, today: date) -> Snapshot:
    month = f"{today:%Y-%m}"
    with engine.begin() as conn:
        led = Ledger(conn, user_id)
        s = led.summarise_month(month)
        c = costs(led, today)
        st = led.sync_status(today)
        tried = [t for t in (st.last_sync_at, st.last_failure_at) if t is not None]
        return Snapshot(
            month,
            st.last_sync_at,
            max(tried, default=None),
            len(led.accounts()),
            s.money_in,
            s.money_out,
            len(ReviewQueue(conn, user_id).items()),
            _pounds(display(c.kind_totals.get("subscription", Fraction(0)))),
            _pounds(display(c.group_totals.get(AI_GROUP, Fraction(0)))),
        )


def cloud_line(now: datetime) -> str:
    """This month's metered cloud spend (#295) against the £9 cap; read only
    when spend.sqlite already exists, so a status never creates it."""
    path = default_db_path().with_name(spend.FILENAME)
    if not path.is_file():
        return f"key stored · nothing spent yet (cap £{CLOUD_CAP})"
    rows = spend.SpendStore(path).month_status(now)
    used = _pounds(sum(b.spent_pence + b.reserved_pence for b in rows))
    return f"{gbp(used)} of £{CLOUD_CAP} this month  {bar(used, CLOUD_CAP, 12)}"


def next_sync_line(s: Snapshot, now: datetime) -> str:
    """When the `fa` server's scheduled sync runs next (#327)."""
    sched = sync_schedule.load()
    due = sync_schedule.next_at(sched, s.last_attempt_at, now)
    if due is None:
        return "off (`fa sync --auto on`)"
    every = f"every {sched.every_hours}h while fa is open"
    if due <= now:
        return f"due now · {every}"
    left = int((due - now).total_seconds()) // 60
    when = f"{left}m" if left < 60 else f"{left // 60}h {left % 60}m"
    return f"in {when} ({due:%H:%M} UTC) · {every}"


def one_line(s: Snapshot, now: datetime) -> str:
    return (
        f"synced {_ago(s.last_sync_at, now)} · {s.accounts} accounts · "
        f"{s.month} in {gbp(s.money_in)} · out {gbp(s.money_out)} · "
        f"{s.review} to review"
    )


def status(
    engine: Engine,
    user_id: int,
    now: datetime | None = None,
    held_models: Callable[[], list[str] | None] | None = None,
) -> int:
    now = now or datetime.now(UTC)
    s = snapshot(engine, user_id, now.date())
    held = (held_models or loaded_models)()
    last_backup = store.status(default_db_path().parent, now).last_backup_at
    cloud = cloud_line(now) if keychain.gemini_key() else "off"
    rows = (
        ("Last sync", _ago(s.last_sync_at, now)),
        ("Next sync", next_sync_line(s, now)),
        ("Accounts", str(s.accounts)),
        (f"{s.month} in", gbp(s.money_in)),
        (f"{s.month} out", gbp(s.money_out)),
        ("To review", str(s.review)),
        ("Subscriptions", f"{gbp(s.subs_monthly)} a month"),
        (
            "AI tools",
            f"{gbp(s.ai_monthly)} of £{AI_CAP} cap  {bar(s.ai_monthly, AI_CAP, 12)}",
        ),
        ("Cloud", cloud),
        (
            "Model loaded",
            "Ollama off" if held is None else (", ".join(held) or "none (idle)"),
        ),
        ("Last backup", _ago(last_backup, now)),
    )
    for k, v in rows:
        print(f"  {k:<15}{v}")
    return 0


def month(engine: Engine, user_id: int, which: str | None) -> int:
    with engine.begin() as conn:
        led = Ledger(conn, user_id)
        m = which or led.latest_month() or f"{date.today():%Y-%m}"
        s = led.summarise_month(m)  # ValueError on a bad month
        b = led.category_breakdown(m)
    print(f"{m}   in {gbp(s.money_in)}   out {gbp(s.money_out)}   net {gbp(s.net)}")
    cats = sorted(b.categories, key=lambda c: c.amount, reverse=True)
    top = max((c.amount for c in cats), default=Decimal(0))
    width = max((len(c.name) for c in cats), default=0)
    for c in cats:
        print(f"  {c.name:<{width}}  {gbp(c.amount):>11}  {bar(c.amount, top)}")
    if b.refunds or b.fees:
        print(f"  Refunds {gbp(b.refunds)} · fees {gbp(b.fees)}")
    return 0


def subs(engine: Engine, user_id: int, today: date | None = None) -> int:
    today = today or date.today()
    with engine.begin() as conn:
        c = costs(Ledger(conn, user_id), today)
    groups = (("active", ("active", "overdue")), ("likely", ("suggested",)))
    for title, wanted in groups:
        items = [i for i in c.items if i.status in wanted]
        if not items:
            continue
        print(f"{title.capitalize()}")
        for i in sorted(items, key=lambda i: -(i.monthly or 0)):
            cost = gbp(_pounds(display(i.monthly))) if i.monthly is not None else "-"
            group = i.entry.group or ""
            name = display_name(i.entry.merchant)
            print(f"  {name:<26}{group:<18}{cost:>10} a month")
    total = _pounds(display(c.kind_totals.get("subscription", Fraction(0))))
    ai = _pounds(display(c.group_totals.get(AI_GROUP, Fraction(0))))
    print(f"Subscriptions {gbp(total)} a month ({gbp(total * 12)} a year)")
    print(f"AI tools      {gbp(ai)} of £{AI_CAP} cap  {bar(ai, AI_CAP, 12)}")
    if c.rate_needed:
        print(f"  {c.rate_needed} left out until an exchange rate is set")
    return 0
