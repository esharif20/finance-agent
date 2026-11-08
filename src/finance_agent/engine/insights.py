"""The Insight pipeline (#150): Ledger reads -> detectors -> stored Insights ->
the ranked, feasible feed.

`refresh` reads the user's Spending and Refunds in GBP pence (a foreign row
still needing an FX rate is left out, never guessed; income and transfers
never count), runs every detector (#148) with the Merchants the user cancelled
or marked not a subscription as `stopped`, and stores what they raise in the
`WINDOW_DAYS` up to the last row. Anything dated after the last row (a renewal
not yet due to be raised) waits for a later refresh. It is idempotent.

`ranked` orders the stored Insights by impact x confidence x relevance
(`engine.ranking`), then drops those a constraint Memory rules out. `shown`
takes the week's slate from that feasible list, with the seeded exploration of
`engine.ope` in its last slot, and logs each exposure with its propensity
(#261, ADR 0015). The bandit (`engine.bandit`, #272) runs in shadow there:
each exposure also logs its context bucket, the bandit's slate and the
bandit's probability of showing that item; the live ranker keeps serving.

Confidences are the detectors' fixed per-rule priors, not calibrated: that
needs outcomes on real Insights, which only the user's own feedback will
give. Relevance carries what feedback teaches meanwhile.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import Connection

from finance_agent.categorise.display_names import display_name
from finance_agent.engine import bandit, detectors, ope, ranking
from finance_agent.ledger import NOT_SUBSCRIPTION, Ledger
from finance_agent.ledger.exposure import Exposures, Shown, week_of
from finance_agent.ledger.insights import Found, Insights, StoredInsight
from finance_agent.ledger.proposals import Proposals

WINDOW_DAYS = 90  # what was raised longer before the last row is history
STOPPED = ("user_cancelled", NOT_SUBSCRIPTION)
COUNTED = ("Spending", "Refund")


def label(category: detectors.Category) -> str:
    return f"{category[0]} / {category[1]}"


def rows(led: Ledger) -> list[detectors.Row]:
    """Spending and Refunds in signed GBP pence, one row per Transaction under
    its Category (a Split counts under its largest part).
    shortcut: a Split is not spread over its parts' Categories; spread it if
    spikes on split Categories turn out to matter."""
    gbp = led.foreign_in_gbp()
    out: list[detectors.Row] = []
    for t in led.transactions():
        c = t.categorisation
        if c is None or c.kind not in COUNTED or c.category is None:
            continue
        minor = int(t.amount.scaleb(2)) if t.currency == "GBP" else gbp.get(t.id)
        if minor is None:
            continue  # rate needed
        out.append(
            detectors.Row(t.id, t.posted_on, c.merchant or "", c.category, minor)
        )
    return out


def stopped(led: Ledger) -> set[str]:
    return {e.merchant for e in led.subscriptions() if e.status in STOPPED}


def found(rs: Iterable[detectors.Row], stop: Collection[str] = ()) -> list[Found]:
    """What the detectors raise in the window ending on the last row, each
    under the Category of its latest evidence (a spike: its own)."""
    rs = list(rs)
    if not rs:
        return []
    last = max(r.posted_on for r in rs)
    first = last - timedelta(days=WINDOW_DAYS)
    by_id = {r.id: r for r in rs}
    out: list[Found] = []
    for i in detectors.detect(rs, stop):
        if not first < i.on <= last or not i.key:  # "" is no Merchant
            continue
        latest = max((by_id[e] for e in i.evidence), key=lambda r: (r.posted_on, r))
        category = i.key if i.type == "category_spike" else label(latest.category)
        out.append(
            Found(i.type, i.key, category, i.on, i.impact, i.confidence, i.evidence)
        )
    return out


def refresh(conn: Connection, user_id: int) -> None:
    led = Ledger(conn, user_id)
    Insights(conn, user_id).replace(found(rows(led), stopped(led)))


@dataclass(frozen=True)
class Ranked:
    insight: StoredInsight
    relevance: ranking.Relevance

    @property
    def score(self) -> Fraction:
        i = self.insight
        return i.impact * i.confidence * self.relevance.mean()


def ranked(conn: Connection, user_id: int) -> list[Ranked]:
    """The current Insights, best first, less those a constraint rules out."""
    store = Insights(conn, user_id)
    cells = ranking.posteriors(store.signals())
    tags = [
        t
        for m in Proposals(conn, user_id).memories()
        if m.type == "constraint"
        for t in m.tags
    ]
    best = ranking.rank(store.current(), cells)
    keep = ranking.feasible(best, lambda i: f"{i.key} {i.category}", tags)
    return [Ranked(i, cells.get((i.type, i.category), ranking.PRIOR)) for i in keep]


def shown(conn: Connection, user_id: int, k: int, day: date) -> list[Ranked]:
    """The `k`-slate shown in `day`'s week, each item logged as an exposure.
    Only feasible Insights are candidates, and exploration swaps a slot, never
    adds one. Listing is not an outcome: nothing here teaches relevance."""
    feed = ranked(conn, user_id)
    slots = ope.slate(feed, k, ope.seed(user_id, week_of(day), ope.POLICY))
    log = Exposures(conn, user_id)
    ctx, would = _shadow(conn, user_id, log, feed, k, day)
    log.log(
        day,
        ope.POLICY,
        (
            Shown(
                "insight",
                str(s.item.insight.id),
                s.slot,
                len(slots),
                s.propensity,
                {
                    "type": s.item.insight.type,
                    "day": day.day,
                    "candidates": len(feed),
                    "cat": bandit.code(s.item.insight.category),
                    "tom": ctx.tom,
                    "spend": ctx.spend,
                    # the bandit's chance of showing it, over DRAWS + 1: OPE's target
                    "ts_q": int(
                        would.membership[str(s.item.insight.id)] * (bandit.DRAWS + 1)
                    ),
                }
                | {f"ts{p.slot}": p.arm.item for p in would.slate},
            )
            for s in slots
        ),
    )
    return [s.item for s in slots]


def _shadow(
    conn: Connection,
    user_id: int,
    log: Exposures,
    feed: list[Ranked],
    k: int,
    day: date,
) -> tuple[bandit.Context, bandit.Decision]:
    """What the bandit (#272) would show this week, in shadow: it learns from
    the explicit outcomes on logged Insights, and serves nothing."""
    spends = [(r.posted_on, r.amount) for r in rows(Ledger(conn, user_id))]
    ctx = bandit.Context(bandit.time_of_month(day), bandit.spend_level(spends, day))
    seen = [
        bandit.Observed(
            str(c["type"]),
            str(c["cat"]),
            bandit.Context(int(c["tom"]), int(c["spend"])),
            e.outcome,
        )
        for e in log.logged("insight")
        if e.outcome is not None
        and {"type", "cat", "tom", "spend"} <= (c := e.context).keys()
    ]
    arms = [
        bandit.Arm(str(r.insight.id), r.insight.type, bandit.code(r.insight.category))
        for r in feed
    ]
    seed = ope.seed(user_id, week_of(day), bandit.POLICY)
    return ctx, bandit.choose(arms, bandit.learn(seen), ctx, k, seed)


def _pounds(pence: int) -> str:
    return f"£{pence // 100:,}.{pence % 100:02d}"


TEMPLATES = {
    "recurring": "{key} costs about {impact} a year.",
    "price_increase": "{key} went up: about {impact} more a year.",
    "category_spike": "{key} is {impact} over your usual this month.",
    "upcoming_renewal": "{key} renews soon; it was {impact} last time.",
}


def shown_key(i: Found) -> str:
    """The key to show: a Merchant's readable name; a spike's Category as is."""
    return i.key if i.type == "category_spike" else display_name(i.key)


def explain(i: Found) -> str:
    """The Insight's explanation: its type's template, with figures from code."""
    return TEMPLATES[i.type].format(key=shown_key(i), impact=_pounds(i.impact))
