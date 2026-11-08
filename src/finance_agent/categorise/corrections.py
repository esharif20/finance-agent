"""Each live Merchant-wide Correction beside what the UK base, the keyword
rules or the model alone would give that Merchant (#215 F): where the user
disagreed with the machine. Ids and Categories only, never a description or
an amount."""

from dataclasses import dataclass

from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.categorise import auto
from finance_agent.ledger import Ledger
from finance_agent.storage import Scoped, schema

type Category = tuple[str, str]


@dataclass(frozen=True)
class Corrected:
    id: int
    merchant_id: int
    category: Category
    base: Category | None  # the UK merchant base's default
    rules: Category | None  # the first keyword rule that matches
    model: Category | None  # the latest logged model guess (any tier)


def corrected(
    conn: Connection, user_id: int, category: Category | None = None
) -> list[Corrected]:
    """Active Merchant-wide Corrections, newest first, this user's only."""
    led = Ledger(conn, user_id)
    latest: dict[int, tuple[int, Category]] = {}
    for k in led.corrections():  # oldest first: the latest per Merchant wins
        if k.merchant_id is not None and k.reverted_at is None:
            latest[k.merchant_id] = (k.id, k.category)
    if category is not None:
        latest = {m: v for m, v in latest.items() if v[1] == category}
    names = {m.id: m.name for m in led.merchants()}
    described: dict[int, str] = {}
    for t in led.transactions():
        c = t.categorisation
        if c is not None and c.merchant_id in latest:
            described.setdefault(c.merchant_id, t.description)
    ids = auto.ids(conn, user_id)
    by_id = {i: c for c, i in ids.items()}
    db = Scoped(conn, user_id)
    p = schema.category_prediction
    guess: dict[int, Category] = {}
    for r in db.execute(
        db.select(p, p.c.merchant_id, p.c.category_id)
        .where(p.c.merchant_id.in_(list(latest)))
        .order_by(p.c.id)
    ):
        cat = by_id.get(r.category_id)
        if cat is not None and cat != categorise.UNCATEGORISED:
            guess[r.merchant_id] = cat

    def rule(m: int) -> Category | None:
        d = categorise._upper(described.get(m, ""))  # pyright: ignore[reportPrivateUsage]
        return next((c for _, p, c in categorise.RULES if p.search(d)), None)

    out = [
        Corrected(
            id=k,
            merchant_id=m,
            category=cat,
            base=base[1]
            if (base := categorise.MERCHANTS.get(names.get(m, "")))
            else None,
            rules=rule(m),
            model=guess.get(m),
        )
        for m, (k, cat) in latest.items()
    ]
    return sorted(out, key=lambda c: -c.id)
