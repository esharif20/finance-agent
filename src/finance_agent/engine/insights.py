"""The Insight pipeline (#150): Ledger reads -> detectors -> stored Insights ->
the ranked, feasible feed.

`refresh` reads the user's Spending and Refunds in GBP pence (a foreign row
still needing an FX rate is left out, never guessed; income and transfers
never count), runs every detector (#148) with the Merchants the user cancelled
or marked not a subscription as `stopped`, and stores what they raise in the
`WINDOW_DAYS` up to the last row. Anything dated after the last row (a renewal
not yet due to be raised) waits for a later refresh. It is idempotent.

`ranked` orders the stored Insights by impact x confidence x relevance
(`engine.ranking`), then drops those a constraint Memory rules out.

Confidences are the detectors' fixed per-rule priors, not calibrated: that
needs outcomes on real Insights, which only the user's own feedback will
give. Relevance carries what feedback teaches meanwhile.
"""

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import timedelta
from fractions import Fraction

from sqlalchemy import Connection

from finance_agent.engine import detectors, ranking
from finance_agent.ledger import NOT_SUBSCRIPTION, Ledger
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


def _pounds(pence: int) -> str:
    return f"£{pence // 100:,}.{pence % 100:02d}"


TEMPLATES = {
    "recurring": "{key} costs about {impact} a year.",
    "price_increase": "{key} went up: about {impact} more a year.",
    "category_spike": "{key} is {impact} over your usual this month.",
    "upcoming_renewal": "{key} renews soon; it was {impact} last time.",
}


def explain(i: Found) -> str:
    """The Insight's explanation: its type's template, with figures from code."""
    return TEMPLATES[i.type].format(key=i.key, impact=_pounds(i.impact))
