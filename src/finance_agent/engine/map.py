"""The personal map (#275, decision 10 of #251): a small entity graph of one
user's Merchants, Categories, goal Memories and confirmed life changes.

The obvious edges are derived on every read from what the Ledger already
holds, so there is no second copy to drift from it:

- **Merchant - Category**: the Merchant has a Transaction in that Category.
- **goal - Category**: one of the goal's tags names the Category (every word
  of a hyphenated tag appears in "top leaf"; a plural matches its singular).
- **life change - series**: the Merchants and Categories of the Transactions
  in the changed series (income, rent, bills or groceries).

Every other edge ("this Merchant relates to the car goal") is a `map_edge`
Proposal the user confirmed (`ledger.map_edges`). `candidates` lists the
Merchants a goal plausibly relates to, for the Assistant to propose from; it
proposes, it never stores.

Functions over ids and names (and `read`, over the Ledger): no figures,
nothing logged.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Connection

from finance_agent.ledger import Ledger
from finance_agent.ledger.life_changes import series_of
from finance_agent.ledger.map_edges import MapEdges, Node, Pair, pair
from finance_agent.ledger.taxonomy import TAXONOMY_VERSION, current

type Source = Literal["derived", "confirmed"]


@dataclass(frozen=True)
class Use:
    """One categorised Transaction as the map reads it."""

    merchant: int | None
    category: int | None
    series: str | None = None


@dataclass(frozen=True, order=True)
class Edge:
    a: Node
    b: Node
    source: Source


def words(text: str) -> frozenset[str]:
    """Casefolded words, a plural as its singular ("Holidays" -> "holiday")."""
    out = "".join(c if c.isalnum() else " " for c in text.casefold()).split()
    return frozenset(w[:-1] if len(w) > 3 and w[-1] == "s" != w[-2] else w for w in out)


def _names(tag: str, name: str) -> bool:
    return bool(w := words(tag)) and w <= words(name)


def derived(
    uses: Iterable[Use],
    categories: Mapping[int, tuple[str, str]],
    goals: Mapping[int, Sequence[str]],
    life_changes: Mapping[int, str],
) -> set[Pair]:
    """Every derived edge. `categories`: id -> (top, leaf); `goals`: goal
    Memory id -> its tags; `life_changes`: life change id -> its series."""
    out: set[Pair] = set()
    by_series: dict[str, set[Node]] = {}
    for u in uses:
        m = Node("merchant", u.merchant) if u.merchant is not None else None
        c = Node("category", u.category) if u.category is not None else None
        if m and c:
            out.add(pair(m, c))
        if u.series is not None:
            by_series.setdefault(u.series, set()).update(n for n in (m, c) if n)
    for gid, tags in goals.items():
        for cid, (top, leaf) in categories.items():
            if any(_names(t, f"{top} {leaf}") for t in tags):
                out.add(pair(Node("goal", gid), Node("category", cid)))
    for lid, series in life_changes.items():
        for n in by_series.get(series, ()):
            out.add(pair(Node("life_change", lid), n))
    return out


def graph(derived_edges: Iterable[Pair], confirmed: Iterable[Pair]) -> list[Edge]:
    """Derived and confirmed edges together, sorted; an edge that is both is
    shown as confirmed."""
    kept = {pair(*p) for p in confirmed}
    every = {p: "confirmed" if p in kept else "derived" for p in derived_edges}
    every |= {p: "confirmed" for p in kept}
    return sorted(Edge(a, b, s) for (a, b), s in every.items())  # type: ignore[arg-type]


def neighbours(node: Node, edges: Iterable[Edge]) -> list[tuple[Node, Source]]:
    """The nodes one edge from `node`, sorted."""
    return sorted(
        (e.b if e.a == node else e.a, e.source) for e in edges if node in (e.a, e.b)
    )


def candidates(
    tags: Sequence[str],
    merchants: Mapping[int, str],
    categories: Mapping[int, tuple[str, str]],
    derived_edges: Iterable[Pair],
) -> set[int]:
    """Merchants a goal with `tags` plausibly relates to: one whose name holds
    a tag's words, or one with a Transaction in a Category the goal names."""
    cats = {
        Node("category", cid)
        for cid, (top, leaf) in categories.items()
        if any(_names(t, f"{top} {leaf}") for t in tags)
    }
    out = {mid for mid, name in merchants.items() if any(_names(t, name) for t in tags)}
    for a, b in derived_edges:
        for c, m in ((a, b), (b, a)):
            if c in cats and m.kind == "merchant":
                out.add(m.id)
    return out


def uses(led: Ledger, categories: Mapping[int, tuple[str, str]]) -> list[Use]:
    """Each categorised Transaction's Merchant, Category (today's names) and
    series."""
    ids = {v: k for k, v in categories.items()}
    out: list[Use] = []
    for t in led.transactions():
        c = t.categorisation
        if c is None:
            continue
        cat = (
            current(c.taxonomy_version, (c.category[0], c.category[1]))
            if c.category
            else None
        )
        out.append(
            Use(
                c.merchant_id,
                ids.get(cat) if cat else None,
                series_of(c.kind, cat, TAXONOMY_VERSION),
            )
        )
    return out


def read(conn: Connection, user_id: int) -> list[Edge]:
    """One user's whole map: derived on this read, plus the confirmed edges."""
    store = MapEdges(conn, user_id)
    cats = store.categories()
    found = derived(
        uses(Ledger(conn, user_id), cats), cats, store.goals(), store.life_changes()
    )
    return graph(found, [(e.a, e.b) for e in store.stored()])
