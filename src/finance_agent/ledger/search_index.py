"""The search index (#253): reads of the FTS5 table `search_fts` and the vector
table `text_embedding` that migration 0026's triggers keep in step with
Transactions, Merchants, live Notes, live Split parts and Memories.

A document id is kind * 2**40 + the source row's id (a Split part's is its
correction id * 10 + ordinal). Every statement here is filtered to one user.
Query text, bodies and vectors are never logged; only counts are. Monthly
stories (#276) are matched here by the words their stored recap names, with no
index table of their own (no migration): a user has one row a month.
"""

import logging
import re
from array import array
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any, NamedTuple

from sqlalchemy import Connection, bindparam, text

from finance_agent.ledger.stories import Stories
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

BASE = 1 << 40  # keep in step with migration 0026
KINDS = ("transaction", "merchant", "note", "split", "memory")  # codes 1..5
_WORD = re.compile(r"\w+")
MAX_TERMS = 16

type Vector = tuple[int, ...]


class Doc(NamedTuple):
    id: int
    kind: str
    ref: int  # the Transaction, Merchant, Note or Memory id; a Split's correction
    body: str
    transaction_id: int | None
    on: date | None  # the Transaction's date, or when a Memory was said
    spend_minor: int  # the Merchant's total spend, pence; 0 when none


def doc_id(kind: str, ref: int, ordinal: int = 0) -> int:
    base = (KINDS.index(kind) + 1) * BASE
    return base + (ref * 10 + ordinal if kind == "split" else ref)


def kind_of(doc: int) -> tuple[str, int]:
    """(kind, ref): a Split part's ref is its correction id."""
    kind, ref = KINDS[doc // BASE - 1], doc % BASE
    return kind, ref // 10 if kind == "split" else ref


def fts_query(query: str) -> str | None:
    """The words of `query` as quoted FTS5 prefix terms joined by OR, so no
    query syntax (quotes, NEAR, *, column filters, AND/OR/NOT) gets through;
    None when it has no words."""
    words = _WORD.findall(query.lower())[:MAX_TERMS]
    return " OR ".join(f'"{w}"*' for w in dict.fromkeys(words)) or None


def match(conn: Connection, user_id: int, query: str, limit: int) -> dict[int, int]:
    """FTS5 matches: document id -> BM25 score in millionths (higher is better)."""
    q = fts_query(query)
    if q is None:
        return {}
    rows = conn.execute(
        text(
            "SELECT rowid, CAST(-rank * 1000000 AS INTEGER) FROM search_fts "
            "WHERE search_fts MATCH :q AND user_id = :u ORDER BY rank, rowid LIMIT :n"
        ),
        {"q": q, "u": user_id, "n": limit},
    )
    return {int(r[0]): int(r[1]) for r in rows}


def bodies(conn: Connection, user_id: int) -> dict[int, str]:
    rows = conn.execute(
        text("SELECT rowid, body FROM search_fts WHERE user_id = :u"), {"u": user_id}
    )
    return {int(r[0]): str(r[1]) for r in rows}


def docs(conn: Connection, user_id: int, ids: Iterable[int]) -> dict[int, Doc]:
    """The documents among `ids` that are this user's, with what the rerank
    reads: the date and the Merchant's spend."""
    ids = sorted(set(ids))
    if not ids:
        return {}
    found = conn.execute(
        text(
            "SELECT rowid, body FROM search_fts WHERE user_id = :u AND rowid IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"u": user_id, "ids": ids},
    ).all()
    refs: dict[str, set[int]] = {k: set() for k in KINDS}
    for r in found:
        kind, ref = kind_of(int(r[0]))
        refs[kind].add(ref)
    db = Scoped(conn, user_id)
    t, c, k, m = (
        schema.transaction,
        schema.categorisation,
        schema.correction,
        schema.memory,
    )
    n = schema.note
    txn_of: dict[tuple[str, int], int | None] = {
        ("transaction", i): i for i in refs["transaction"]
    }
    for kind, table in (("note", n), ("split", k)):
        if refs[kind]:
            q = db.select(table, table.c.id, table.c.transaction_id).where(
                table.c.id.in_(refs[kind])
            )
            txn_of |= {(kind, r.id): r.transaction_id for r in db.execute(q)}
    dates: dict[int, date] = {}
    merchant_of: dict[int, int] = {}
    if txns := {v for v in txn_of.values() if v is not None}:
        q = (
            db.select(t, t.c.id, t.c.posted_on, c.c.merchant_id)
            .outerjoin(c, db.on(c, c.c.transaction_id == t.c.id))
            .where(t.c.id.in_(txns))
        )
        for r in db.execute(q):
            dates[r.id] = r.posted_on
            if r.merchant_id is not None:
                merchant_of[r.id] = r.merchant_id
    merchants = refs["merchant"] | set(merchant_of.values())
    spend: dict[int, int] = {}
    latest: dict[int, date] = {}
    if merchants:
        q = (
            db.select(c, c.c.merchant_id, t.c.amount_minor, t.c.posted_on)
            .join(t, db.on(t, t.c.id == c.c.transaction_id))
            .where(c.c.merchant_id.in_(merchants))
        )
        for r in db.execute(q):
            spend[r.merchant_id] = spend.get(r.merchant_id, 0) + max(0, -r.amount_minor)
            latest[r.merchant_id] = max(
                latest.get(r.merchant_id, r.posted_on), r.posted_on
            )
    said: dict[int, date] = {}
    if refs["memory"]:
        q = db.select(m, m.c.id, m.c.said_on).where(m.c.id.in_(refs["memory"]))
        said = {r.id: r.said_on for r in db.execute(q)}
    out: dict[int, Doc] = {}
    for r in found:
        did, body = int(r[0]), str(r[1])
        kind, ref = kind_of(did)
        tid = txn_of.get((kind, ref))
        mid = ref if kind == "merchant" else merchant_of.get(tid or 0)
        on = (
            said.get(ref)
            if kind == "memory"
            else latest.get(ref)
            if kind == "merchant"
            else dates.get(tid or 0)
        )
        sp = spend.get(mid, 0) if mid is not None else 0
        out[did] = Doc(did, kind, ref, body, tid, on, sp)
    return out


# --- vectors -----------------------------------------------------------------


# shortcut: native byte order, as merchant_embedding (categorise/embedding.py)
def _pack(v: Vector) -> bytes:
    return array("h", v).tobytes()


def _unpack(blob: bytes) -> Vector:
    a = array("h")
    a.frombytes(blob)
    return tuple(a)


def pending(conn: Connection, user_id: int, model: str) -> dict[int, str]:
    """Documents (id -> body) with no vector from `model` yet."""
    db = Scoped(conn, user_id)
    te = schema.text_embedding
    have = set(
        db.execute(db.select(te, te.c.doc_id).where(te.c.model == model)).scalars()
    )
    return {i: b for i, b in bodies(conn, user_id).items() if i not in have}


def store(
    conn: Connection, user_id: int, model: str, vecs: Mapping[int, Vector]
) -> int:
    """Write vectors, skipping any document deleted since `pending`."""
    db = Scoped(conn, user_id)
    te = schema.text_embedding
    live = set(docs_present(conn, user_id, vecs))
    db.execute(db.delete(te).where(te.c.doc_id.in_(live)))
    for i in sorted(live):  # (deleting first drops another model's vector)
        v = vecs[i]
        db.insert(te, doc_id=i, model=model, dims=len(v), vector=_pack(v))
    log.info("search vectors stored=%d", len(live))
    return len(live)


def docs_present(conn: Connection, user_id: int, ids: Iterable[int]) -> list[int]:
    ids = sorted(set(ids))
    if not ids:
        return []
    rows = conn.execute(
        text(
            "SELECT rowid FROM search_fts WHERE user_id = :u AND rowid IN :ids"
        ).bindparams(bindparam("ids", expanding=True)),
        {"u": user_id, "ids": ids},
    )
    return [int(r[0]) for r in rows]


def vectors(conn: Connection, user_id: int, model: str) -> dict[int, Vector]:
    """This user's vectors from `model`, by document id; identical vectors are
    one tuple, so a caller can score each distinct vector once."""
    db = Scoped(conn, user_id)
    te = schema.text_embedding
    rows = db.execute(
        db.select(te, te.c.doc_id, te.c.vector).where(te.c.model == model)
    )
    seen: dict[bytes, Vector] = {}
    return {r.doc_id: seen.setdefault(r.vector, _unpack(r.vector)) for r in rows}


def forget(conn: Connection, user_id: int) -> int:
    """Delete every search vector of this user (derived; rebuilt on refresh)."""
    db = Scoped(conn, user_id)
    n = db.execute(db.delete(schema.text_embedding)).rowcount
    log.info("search vectors forgotten=%d", n)
    return n


def count(conn: Connection, user_id: int, kinds: Sequence[str] = KINDS) -> int:
    """How many documents of `kinds` this user has (for tests and status)."""
    return sum(
        int(
            conn.execute(
                text(
                    "SELECT count(*) FROM search_fts WHERE user_id = :u "
                    "AND rowid BETWEEN :lo AND :hi"
                ),
                {"u": user_id, "lo": doc_id(k, 0), "hi": doc_id(k, 0) + BASE - 1},
            ).scalar_one()
        )
        for k in kinds
    )


# --- monthly stories (#276) --------------------------------------------------


def story_items(recap: Mapping[str, Any]) -> list[tuple[set[str], int]]:
    """What a stored recap names, one item per change or Insight: its words
    (Category, up or down, driving Merchants; an Insight's Merchant, Category
    and type) and its size in pence. Wording is left out: its everyday words
    would match every question."""
    items: list[tuple[str, int]] = []
    for c in recap.get("changes", ()):
        way = "up more rose" if c["change"] > 0 else "down less fell"
        names = [c["category"], way, *(d["merchant"] for d in c.get("drivers", ()))]
        items.append((" ".join(names), abs(c["change"])))
    for a in recap.get("anomalies", ()):
        kind = a["type"].replace("_", " ")
        items.append((f"{a['key']} {a['category']} {kind}", abs(a["impact"])))
    return [(set(_WORD.findall(t.lower())), size) for t, size in items]


def story_rank(recaps: Mapping[str, Mapping[str, Any]], query: str) -> list[str]:
    """Months by their best item: the most words of `query` it names
    (prefixes of two letters or more), then the largest change, then the
    earliest month. Months naming none are left out."""
    words = {w for w in _WORD.findall(query.lower()) if len(w) >= 2}
    scored: list[tuple[int, int, str]] = []
    for month, recap in recaps.items():
        best = max(
            (
                (sum(any(h.startswith(w) for h in have) for w in words), size)
                for have, size in story_items(recap)
            ),
            default=(0, 0),
        )
        if best[0]:
            scored.append((-best[0], -best[1], month))
    return [m for *_, m in sorted(scored)]


def story_months(conn: Connection, user_id: int, query: str) -> list[str]:
    """`story_rank` over this user's stored monthly stories."""
    s = Stories(conn, user_id)
    found = {m: got.recap for m in s.months() if (got := s.get(m)) is not None}
    return story_rank(found, query)
