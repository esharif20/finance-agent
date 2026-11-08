"""The planner (#317): a question becomes a typed `Plan` in code, from the
references `resolve` settled, and the Engine answers it.

- A figure question ("how much did I spend on food this year?") is answered
  from a template: no model call.
- A follow-up ("and the month before?", "what about Uber?", "by merchant",
  "compared to last year") patches the thread's last Plan: no model call.
- An open question ("where can I save?") or an in-depth one ("plan my next 3
  months") gets one fetch round of at most `MAX_FETCHES` Engine calls, and
  one writer call with no tool schemas (graph.py).

A Plan holds selectors and labels, never a figure, so the graph can keep the
last one in the thread's checkpoint. Every figure a template writes comes from
an Engine result in `Fetched`, and the reply still passes grounding (ADR 0007).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from finance_agent.assistant import resolve as rs
from finance_agent.assistant.chat_model import ToolCall
from finance_agent.assistant.grounding import (
    Money,
    Month,
    Percent,
    ToolValue,
    format_figure,
)
from finance_agent.assistant.tools.query import QueryPlan, SpendForQueryOut, spend
from finance_agent.assistant.tools.read import (
    Data,
    FindTransactionsOut,
    Source,
    SpendingSummaryIn,
    SpendingSummaryOut,
    spending_summary,
    txn_row,
)
from finance_agent.assistant.tools.read import Money as ToolMoney
from finance_agent.ledger import CURRENCY, Ledger, fx
from finance_agent.ledger.proposals import Memory

MAX_FETCHES = 3  # one parallel round for an open or in-depth question
TOP = 3  # merchants named in a breakdown
_STRICT = ConfigDict(extra="forbid", frozen=True)

type Kind = Literal["figure", "income", "big", "balances", "open", "in_depth"]
type Ask = Literal["overdraft", "savings", "net", "balance"]


class Plan(BaseModel):
    """Selectors and labels only: never a figure (checkpointed)."""

    model_config = _STRICT
    kind: Kind
    since: date
    until: date
    categories: tuple[str, ...] = ()
    merchant_ids: tuple[int, ...] = ()
    what: str = ""  # the label of the Categories and Merchants
    account_id: int | None = None
    compare: rs.Compare | None = None
    group: Literal["merchant"] | None = None
    currency: str | None = None
    asks: Ask | None = None  # a balances Plan: which question about them
    labels: tuple[str, ...] = ()  # every resolution, shown with the answer

    @property
    def period(self) -> rs.Period:
        return rs.Period(
            self.since, self.until, rs.period_label(self.since, self.until)
        )


@dataclass(frozen=True)
class Clarify:
    question: str


@dataclass(frozen=True)
class Fetched:
    name: str  # the tool or Engine read it came from
    out: BaseModel


# --- classifying ---------------------------------------------------------------

# a write, a payment or an amount the user names: never planned here
_WRITE = re.compile(
    r"\b(move|send|transfer|pay (?:off|for)|remember|split|recategori[sz]e"
    r"|categori[sz]e|note|ignore|forget|cancel|make me|afford|actually)\b|£|\$|€",
    re.I,
)
_OPEN = re.compile(
    r"^(?:where|how) (?:can|could|should) i (?:save|cut back|spend less)"
    r"|\bhow am i doing\b|\bwhat should i cut\b|\bwhere is my money going\b"
    r"|\bhow (?:is|are) my (?:money|finances|spending) (?:doing|looking)\b"
    r"|\bany (?:tips|ideas) to save\b",
    re.I,
)
_IN_DEPTH = re.compile(
    r"\bplan (?:out )?my next (?:\d+|two|three|six) months\b|\bin[- ]depth\b"
    r"|\bdeep dive\b|\breview my (?:finances|money|spending)\b",
    re.I,
)
_FIGURE = re.compile(
    r"^(?:how much (?:did|have|do) i (?:spend|spent|pay|paid)"
    r"(?: (?:on|at|for|in|with))?|how much (?:did|has|have) (?P<what>.+?) cost(?: me)?)"
    r"(?P<rest>.*?)\s*\??$",
    re.I,
)
_INCOME = re.compile(
    r"^(?:how much|what) (?:came in|did i earn|was my (?:salary|income))", re.I
)


def classify(message: str) -> Literal["open", "in_depth"] | None:
    m = " ".join(message.split())
    if _WRITE.search(m):
        return None
    if _IN_DEPTH.search(m):
        return "in_depth"
    return "open" if _OPEN.search(m) else None


def _default_period(today: date) -> rs.Period:
    start = date(today.year, today.month, 1)
    return rs.Period(start, today, rs.period_label(start, today))


def _from(r: rs.Resolution, kind: Kind, today: date) -> Plan:
    p = r.period or _default_period(today)
    if r.period is None:  # the slot default, stated
        r.labels.insert(0, p.label)
    what = " + ".join(
        x for x in (rs.category_label(r.categories), r.merchant_label) if x
    )
    return Plan(
        kind=kind,
        since=p.since,
        until=p.until,
        categories=r.categories,
        merchant_ids=r.merchant_ids,
        what=what,
        account_id=r.account_id,
        compare=r.compare,
        currency=r.currency,
        labels=tuple(r.labels),
    )


def figure(
    message: str, today: date, led: Ledger, memories: Sequence[Memory] = ()
) -> Plan | Clarify | None:
    """A figure question as a Plan, or the one question to ask, or None."""
    m = " ".join(message.split())
    if _WRITE.search(m):
        return None
    kind: Kind
    if _INCOME.search(m):
        body = _INCOME.sub("", m)
        r, kind = rs.resolve(m, today, led, memories), "income"
        r.income = True
    elif fm := _FIGURE.match(m):
        body = f"{fm['what'] or ''} {fm['rest']}"
        r = rs.resolve(body, today, led, memories)
        kind = "income" if r.income else "big" if r.threshold_minor else "figure"
    else:
        return None
    if r.question:
        return Clarify(r.question)
    if rs.unresolved(body, r):
        return None  # a word no resolver read: the model, which may ask
    one_month = r.period is not None and len(set(r.period.months())) == 1
    if kind == "figure" and not (r.categories or r.merchant_ids or one_month):
        return None  # a total over several months: the model
    if r.account_id is not None and not (r.categories or r.merchant_ids):
        return None
    plan = _from(r, kind, today)
    return plan if plan.period.whole(today) else None


# --- follow-ups ------------------------------------------------------------------

_FOLLOW = re.compile(
    r"^(?:and|what about|how about|ok|okay|so)\b|^(?:by|vs|compared?)\b", re.I
)


def follow_up(
    prev: Plan, message: str, today: date, led: Ledger, memories: Sequence[Memory] = ()
) -> Plan | Clarify | None:
    """Patch the last Plan for an elliptical follow-up; None when it isn't one."""
    m = " ".join(message.split()).rstrip("?!. ")
    if prev.kind in ("open", "in_depth") or _WRITE.search(m) or len(m.split()) > 8:
        return None
    if not _FOLLOW.search(m) and not re.search(
        r"\b(?:month before|previous month)\b", m, re.I
    ):
        return None
    low = m.casefold()
    upd: dict[str, object] = {}
    labels = [lb for lb in prev.labels if lb != prev.period.label]
    if re.search(r"\b(?:the )?(?:month before|previous month)\b", low):
        p = rs.month_period(rs._shift(prev.since, -1))  # pyright: ignore[reportPrivateUsage]
        upd |= {"since": p.since, "until": p.until}
    elif re.search(
        r"\b(?:compared? (?:to|with)|vs\.?) (?:last|a) year\b|\byear on year\b", low
    ):
        upd["compare"] = "year"
    elif re.search(
        r"\b(?:compared? (?:to|with)|vs\.?) (?:usual|normal|average)\b", low
    ):
        upd["compare"] = "median3"
        labels.append("vs your 3-month median")
    elif re.search(r"\bby merchant\b|\bwhich merchants\b|\bwhere\b", low):
        upd["group"] = "merchant"
    elif (p := rs.period(m, today)) is not None:
        upd |= {"since": p.since, "until": p.until}
    else:
        body = re.sub(r"^(?:and|what about|how about|ok|okay|so)\s+", "", m, flags=re.I)
        r = rs.resolve(body, today, led, memories)
        if r.question:
            return Clarify(r.question)
        if not (r.categories or r.merchant_ids):
            return None
        new = _from(r, prev.kind if prev.kind != "big" else "figure", today)
        upd |= {
            "categories": new.categories,
            "merchant_ids": new.merchant_ids,
            "what": new.what,
        }
        labels = [lb for lb in new.labels if lb != new.period.label]
    out = prev.model_copy(update=upd)
    if not out.period.whole(today):
        return None
    return out.model_copy(update={"labels": (out.period.label, *labels)})


# --- running a figure Plan --------------------------------------------------------


class Median(BaseModel):
    model_config = _STRICT
    evidence_id: str
    months: list[str]
    median: ToolMoney  # the middle of the three months' totals


class Converted(BaseModel):
    model_config = _STRICT
    evidence_id: str
    amount: ToolMoney
    rate_on: date


class IncomeOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    since: str
    until: str
    total: ToolMoney


def _qp(plan: Plan, since: date, until: date) -> QueryPlan:
    return QueryPlan(
        categories=plan.categories,
        merchant_ids=plan.merchant_ids,
        since=f"{since:%Y-%m}",
        until=f"{until:%Y-%m}",
        account_id=plan.account_id,
    )


def _months_back(d: date, n: int) -> date:
    return rs._shift(date(d.year, d.month, 1), -n)  # pyright: ignore[reportPrivateUsage]


def _total(out: BaseModel) -> int:
    if isinstance(out, SpendForQueryOut):
        return out.total.minor
    if isinstance(out, SpendingSummaryOut):
        return out.spending.minor
    if isinstance(out, IncomeOut):
        return out.total.minor
    raise TypeError(type(out).__name__)


def _summary(src: Source, month: str) -> SpendingSummaryOut:
    led = Ledger(src.conn, src.user_id)
    return spending_summary(led, SpendingSummaryIn(month=month))  # pyright: ignore[reportArgumentType]


def _spend(src: Source, plan: Plan, since: date, until: date) -> BaseModel:
    if plan.kind == "income":
        return _income(src, plan, since, until)
    if not (plan.categories or plan.merchant_ids):
        return _summary(src, f"{since:%Y-%m}")
    return spend(src, _qp(plan, since, until))


def _income(src: Source, plan: Plan, since: date, until: date) -> IncomeOut:
    end = rs._month_end(until)  # pyright: ignore[reportPrivateUsage]
    total = sum(
        int(t.amount.scaleb(2))
        for t in Ledger(src.conn, src.user_id).transactions()
        if (c := t.categorisation)
        and c.kind == "Income"
        and t.currency == CURRENCY
        and since <= t.posted_on <= end
        and (not plan.merchant_ids or c.merchant_id in plan.merchant_ids)
        and plan.account_id in (None, t.account_id)
    )
    ids = ",".join(map(str, plan.merchant_ids)) or "all"
    return IncomeOut(
        evidence_id=f"income:{since:%Y-%m}..{until:%Y-%m}:{ids}",
        since=f"{since:%Y-%m}",
        until=f"{until:%Y-%m}",
        total=ToolMoney(minor=total, currency=CURRENCY),
    )


def _big(src: Source, plan: Plan) -> FindTransactionsOut:
    led = Ledger(src.conn, src.user_id)
    th = rs.big_threshold(led) or 0
    end = rs._month_end(plan.until)  # pyright: ignore[reportPrivateUsage]
    rows = sorted(
        (
            t
            for t in led.transactions()
            if (c := t.categorisation)
            and c.kind == "Spending"
            and t.currency == CURRENCY
            and plan.since <= t.posted_on <= end
            and -t.amount.scaleb(2) >= th
        ),
        key=lambda t: (t.amount, t.id),
    )
    return FindTransactionsOut(
        rows=[txn_row(t) for t in rows[:TOP]],
        total=len(rows),
        truncated=len(rows) > TOP,
    )


def execute(plan: Plan, src: Source) -> list[Fetched]:
    """The Engine reads a figure Plan needs: at most `MAX_FETCHES`."""
    if plan.kind == "big":
        return [Fetched("find_transactions", _big(src, plan))]
    base = _spend(src, plan, plan.since, plan.until)
    out = [Fetched(_name(base), base)]
    span = (
        (plan.until.year - plan.since.year) * 12
        + plan.until.month
        - plan.since.month
        + 1
    )
    if plan.compare == "year":
        other = _spend(
            src, plan, _months_back(plan.since, 12), _months_back(plan.until, 12)
        )
        out.append(Fetched(_name(other), other))
    elif plan.compare == "previous":
        other = _spend(
            src, plan, _months_back(plan.since, span), _months_back(plan.until, span)
        )
        out.append(Fetched(_name(other), other))
    elif plan.compare == "median3":
        months = [_months_back(plan.since, k) for k in (3, 2, 1)]
        totals = sorted(_total(_spend(src, plan, d, d)) for d in months)
        tag = ",".join(f"{d:%Y-%m}" for d in months)
        out.append(
            Fetched(
                "median",
                Median(
                    evidence_id=f"median:{tag}",
                    months=[f"{d:%Y-%m}" for d in months],
                    median=ToolMoney(minor=totals[1], currency=CURRENCY),
                ),
            )
        )
    if plan.currency and (c := _convert(src, _total(base), plan.currency, plan.until)):
        out.append(Fetched("fx", c))
    return out[:MAX_FETCHES]


def _name(out: BaseModel) -> str:
    if isinstance(out, SpendForQueryOut):
        return "spend_for_query"
    return "spending_summary" if isinstance(out, SpendingSummaryOut) else "income"


def _convert(src: Source, minor: int, currency: str, on: date) -> Converted | None:
    """The Engine's FX: the latest saved rate on or before the period's end."""
    led = Ledger(src.conn, src.user_id)
    rate = fx.pick(led.fx_rates(), currency, max(on, src.today))
    if rate is None:
        return None
    foreign = fx.convert(minor, Decimal(1) / rate.gbp_per_unit)
    return Converted(
        evidence_id=f"fx:{currency}:{rate.on.isoformat()}",
        amount=ToolMoney(minor=foreign, currency=currency),
        rate_on=rate.on,
    )


# --- templates -----------------------------------------------------------------


def _money(m: ToolMoney) -> str:
    return format_figure(Money(minor=m.minor, currency=m.currency))


def _when(since: date, until: date) -> str:
    if since.day == 1 and since.month == until.month and since.year == until.year:
        return "in " + format_figure(Month(year=since.year, month=since.month))
    return f"from {format_figure(since)} to {format_figure(until)}"


def _period_of(out: BaseModel, plan: Plan) -> str:
    if isinstance(out, SpendForQueryOut | IncomeOut):
        s, u = (
            date.fromisoformat(out.since + "-01"),
            date.fromisoformat(out.until + "-01"),
        )
        same = u == date(plan.until.year, plan.until.month, 1)
        return _when(s, plan.until if same else rs._month_end(u))  # pyright: ignore[reportPrivateUsage]
    if isinstance(out, SpendingSummaryOut):
        y, mo = int(out.month[:4]), int(out.month[5:])
        return "in " + format_figure(Month(year=y, month=mo))
    return ""


def render(plan: Plan, fetched: Sequence[Fetched]) -> str | None:
    """The template reply for a figure Plan, from the Engine's results only."""
    if not fetched:
        return None
    first = fetched[0].out
    if isinstance(first, FindTransactionsOut):
        shown = first.rows
        label = next((lb for lb in plan.labels if lb.startswith("over ")), "")
        noun = "payment" if first.total == 1 else "payments"
        head = f"{first.total} {noun} {label} {_when(plan.since, plan.until)}."
        if shown:
            r = shown[0]
            who = r.merchant.data if r.merchant else r.description.data
            day = format_figure(date.fromisoformat(r.posted_on))
            head += f" The largest was {who}, {_money(r.amount).lstrip('-')} on {day}."
        return " ".join(head.split())
    what = f" on {plan.what}" if plan.what else ""
    verb = "came in" if plan.kind == "income" else "spent"
    total = _money(_amount(first))
    text = (
        f"{total} came in{what.replace(' on ', ' from ', 1)} {_period_of(first, plan)}."
        if verb == "came in"
        else f"You spent {total}{what} {_period_of(first, plan)}."
    )
    for f in fetched[1:]:
        if isinstance(f.out, Median):
            text += f" Your 3-month median is {_money(f.out.median)}."
        elif isinstance(f.out, Converted):
            on = format_figure(f.out.rate_on)
            text += f" That's {_money(f.out.amount)} at the rate of {on}."
        else:
            text += f" It was {_money(_amount(f.out))} {_period_of(f.out, plan)}."
    if (
        plan.group == "merchant"
        and isinstance(first, SpendForQueryOut)
        and first.merchants
    ):
        rows = first.merchants[:TOP]
        parts = [f"{r.merchant.data} ({_money(r.amount)})" for r in rows]
        text += " Most went to " + _join(parts) + "."
    return text


def _amount(out: BaseModel) -> ToolMoney:
    if isinstance(out, SpendForQueryOut | IncomeOut):
        return out.total
    if isinstance(out, SpendingSummaryOut):
        return out.spending
    raise TypeError(type(out).__name__)


def _join(parts: Sequence[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# --- open and in-depth: one fetch round ---------------------------------------------


def open_plan(
    kind: Literal["open", "in_depth"],
    message: str,
    today: date,
    led: Ledger,
    memories: Sequence[Memory] = (),
) -> Plan:
    r = rs.resolve(message, today, led, memories)
    r.question = None  # an open question defaults; it never asks
    return _from(r, kind, today)


def fetches(plan: Plan) -> list[tuple[Literal["ask", "plan"], ToolCall]]:
    """(route, call) pairs for one fetch round: read-only tools only."""
    month = {"month": f"{plan.until:%Y-%m}"}
    first = (
        ToolCall(
            name="category_detail", arguments={"category": plan.categories[0], **month}
        )
        if plan.categories
        else ToolCall(name="spending_summary", arguments=dict(month))
    )
    guidance = ToolCall(name="list_guidance", arguments={"limit": 3})
    if plan.kind == "in_depth":
        ahead = ToolCall(name="forecast_cashflow", arguments={"months": 3})
        return [("plan", ahead), ("ask", first), ("ask", guidance)]
    insights = ToolCall(name="list_insights", arguments={"limit": 3})
    return [("ask", first), ("ask", guidance), ("ask", insights)]


def values(plan: Plan, led: Ledger) -> list[ToolValue]:
    """The figures a Plan's labels and template show that no Engine read
    returned: its dates, the "big" threshold and the median's month count."""
    r = rs.Resolution()
    rs.period_values(r, plan.period)
    if plan.kind == "big" and (th := rs.big_threshold(led)) is not None:
        r.value("threshold", Money(minor=th, currency=CURRENCY))
        r.value("share", Percent(value=Decimal(rs.BIG_SHARE)))
    if plan.compare == "median3":
        r.value("months", 3)
    return r.values


# --- balances (0 calls) --------------------------------------------------------

_BALANCE = re.compile(
    r"\b(?P<over>overdraft|overdrawn|in the red|in debt on my (?:current )?account)\b"
    r"|\b(?P<net>net worth)\b"
    r"|\bhow much (?:is|have i got|do i have) in my (?P<sav>savings?)\b"
    r"|\b(?:my )?(?P<sav2>savings?) balance\b"
    r"|\bwhat(?:'s| is| are) my (?:account )?balances?\b|\bmy balances?\b"
    r"|\bhow much (?:money )?(?:do i have|have i got)\b",
    re.I,
)


def balances_plan(message: str, today: date) -> Plan | None:
    """'how much overdraft am I in', 'what's my balance', 'am I in the red',
    'how much is in my savings', 'net worth': the Accounts' Bank balances."""
    m = _BALANCE.search(" ".join(message.split()))
    if m is None or _WRITE.search(message):
        return None
    asks: Ask = (
        "overdraft"
        if m["over"]
        else "net"
        if m["net"]
        else "savings"
        if m["sav"] or m["sav2"]
        else "balance"
    )
    return Plan(kind="balances", since=today, until=today, asks=asks)


class BalanceRow(BaseModel):
    model_config = _STRICT
    evidence_id: str
    account: Data  # the Bank and the Account's own name: user text
    kind: str
    balance: ToolMoney | None  # the Bank's last running balance
    overdrawn_by: ToolMoney | None  # positive, when the balance is below zero
    as_of: date | None  # the newest statement or sync that holds it


class BalancesOut(BaseModel):
    model_config = _STRICT
    evidence_id: str
    rows: list[BalanceRow]
    overdrawn: ToolMoney  # the overdrawn Accounts' total, in GBP
    total: ToolMoney | None  # every GBP balance, when all are known


def balances(src: Source) -> BalancesOut:
    """Each Account's Bank balance, as /api/accounts shows it. No overdraft
    limit is in the data, so none is ever stated."""
    led = Ledger(src.conn, src.user_id)
    synced = led.sync_status().last_sync_at
    rows: list[BalanceRow] = []
    for a in led.accounts():
        bal = None if a.bank_balance is None else int(a.bank_balance.scaleb(2))
        seen = a.last_import or synced
        rows.append(
            BalanceRow(
                evidence_id=f"account:{a.id}",
                account=Data(data=_account_name(a.bank, a.name, a.kind)),
                kind=a.kind,
                balance=None
                if bal is None
                else ToolMoney(minor=bal, currency=a.currency),
                overdrawn_by=ToolMoney(minor=-bal, currency=a.currency)
                if bal is not None and bal < 0
                else None,
                as_of=seen.date() if seen else None,
            )
        )
    gbp = [r for r in rows if r.balance and r.balance.currency == CURRENCY]
    over = sum(r.overdrawn_by.minor for r in gbp if r.overdrawn_by)
    whole = len(gbp) == len(rows) and all(r.balance for r in rows)
    total = sum(r.balance.minor for r in gbp if r.balance)
    return BalancesOut(
        evidence_id="balances",
        rows=rows,
        overdrawn=ToolMoney(minor=over, currency=CURRENCY),
        total=ToolMoney(minor=total, currency=CURRENCY) if whole and rows else None,
    )


def _as_of(r: BalanceRow) -> str:
    return f" (balance as of {format_figure(r.as_of)})" if r.as_of else ""


NO_LIMIT = "I don't know your overdraft limit, so I can't say how much of it is left."


def render_balances(plan: Plan, out: BalancesOut) -> str:
    known = [r for r in out.rows if r.balance is not None]
    if not known:
        return "None of your Banks' exports gives a balance, so I can't see one."
    if plan.asks == "overdraft":
        over = [r for r in known if r.overdrawn_by]
        if not over:
            listed = "; ".join(
                f"{r.account.data} {_money(r.balance)}{_as_of(r)}"  # pyright: ignore[reportArgumentType]
                for r in known
            )
            return f"None of your accounts is overdrawn: {listed}."
        lines = [
            f"Your {r.account.data} is {_money(r.overdrawn_by)} overdrawn{_as_of(r)}."  # pyright: ignore[reportArgumentType]
            for r in over
        ]
        if len(over) > 1:
            lines.append(f"In all you're {_money(out.overdrawn)} overdrawn.")
        return " ".join([*lines, NO_LIMIT])
    if plan.asks == "savings":
        known = [r for r in known if "saving" in r.kind.casefold()]
        if not known:
            return "I can't see a savings account in your data."
    lines = [f"{r.account.data}: {_money(r.balance)}{_as_of(r)}." for r in known]  # pyright: ignore[reportArgumentType]
    if plan.asks == "net":
        if out.total is None:
            return " ".join(
                [*lines, "I can't total them: one has no balance or another currency."]
            )
        return (
            f"Across your accounts you have {_money(out.total)}. That's your account"
            " balances only: I don't hold investments, pensions or loans."
        )
    if len(known) > 1 and out.total is not None and plan.asks == "balance":
        lines.append(f"In all: {_money(out.total)}.")
    return " ".join(lines)


def _account_name(bank: str, name: str, kind: str) -> str:
    """The Bank and the Account's own name; a name holding its masked number
    ("Account ••••0001") becomes its kind, as its digits are no figure."""
    if any(ch.isdigit() for ch in name):
        name = f"{kind.replace('_', ' ')} account"
    return " ".join(f"{bank} {name}".split())
