"""Hybrid search (#253, AI_DESIGN, ADR 0015): SQLite FTS5 and local text
embeddings over one user's Transactions, Merchants, Notes, Split parts and
Memories, fused by reciprocal rank fusion, then a deterministic rerank.

- **FTS**: the query's words as quoted prefix terms (`search_index.fts_query`),
  ranked by BM25.
- **Vector**: cosine of the query's embedding against every stored document
  vector of the same model (`categorise.embedding`: nomic-embed-text through
  localhost Ollama, `TrigramEmbedder` in CI). No model, no vector list.
- **RRF**: each list contributes 1 / (K + rank); tied scores share a rank
  (competition ranking), so the fusion never depends on tie order. Exact
  `Fraction`s, so nothing depends on summation order either.
- **Rerank**: an exact match first (the whole body, then every query word as a
  whole word), then the fused score, the more recent, the higher Merchant
  spend, the lower document id.

Results are document ids with a 60-character sanitised snippet, quoted as data
(`Snippet`), never instructions. The query and results are never logged.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Literal

from sqlalchemy import Connection

from finance_agent.assistant.tools.sanitise import sanitise
from finance_agent.categorise.embedding import (
    Embedder,
    EmbeddingUnavailable,
    similarity,
    stored_model,
)
from finance_agent.ledger import search_index as ix

K = 60  # the usual RRF constant (Cormack et al. 2009)
DEPTH = 50  # candidates taken from each list
LIMIT = 10
_WORD = re.compile(r"\w+")

type Mode = Literal["hybrid", "fts", "vector"]


@dataclass(frozen=True)
class Snippet:
    """Stored text quoted as data for the UI or the model; never instructions."""

    data: str


@dataclass(frozen=True)
class Hit:
    doc: int
    kind: str
    ref: int  # Transaction, Merchant, Note or Memory id; a Split part's correction
    transaction_id: int | None
    snippet: Snippet


def ranks(scores: Mapping[int, int | Fraction]) -> dict[int, int]:
    """Competition ranks (1 = best) of higher-is-better scores: ties share one."""
    ordered = sorted(scores.values(), reverse=True)
    first: dict[int | Fraction, int] = {}
    for i, s in enumerate(ordered, 1):
        first.setdefault(s, i)
    return {d: first[s] for d, s in scores.items()}


def rrf(*lists: Mapping[int, int | Fraction]) -> dict[int, Fraction]:
    """Reciprocal rank fusion of score maps (document id -> score)."""
    fused: dict[int, Fraction] = {}
    for scores in lists:
        for d, r in ranks(scores).items():
            fused[d] = fused.get(d, Fraction(0)) + Fraction(1, K + r)
    return fused


def _words(s: str) -> list[str]:
    return _WORD.findall(s.lower())


def exact(query: str, body: str) -> int:
    """2: the body is the query; 1: every query word is a word of the body."""
    q, b = _words(query), _words(body)
    if not q:
        return 0
    if q == b:
        return 2
    return 1 if set(q) <= set(b) else 0


def rerank(
    query: str, fused: Mapping[int, Fraction], docs: Mapping[int, ix.Doc]
) -> list[int]:
    def key(d: int) -> tuple[int, Fraction, int, int, int]:
        doc = docs[d]
        return (
            -exact(query, doc.body),
            -fused[d],
            -(doc.on or date.min).toordinal(),
            -doc.spend_minor,
            d,
        )

    return sorted((d for d in fused if d in docs), key=key)


def _top(scores: Mapping[int, int], depth: int = DEPTH) -> dict[int, int]:
    return dict(sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:depth])


def vector_scores(
    conn: Connection, user_id: int, query: str, embedder: Embedder | None
) -> dict[int, int]:
    """Cosine (thousandths) of the query against each stored vector of the
    embedder's model; {} when the local model is off."""
    if embedder is None or (model := stored_model(embedder)) is None:
        return {}
    stored = ix.vectors(conn, user_id, model)
    if not stored:
        return {}
    try:
        (q,) = embedder.embed([query])
    except EmbeddingUnavailable:
        return {}
    by_vec: dict[int, int] = {}  # identical descriptions share one vector
    out: dict[int, int] = {}
    for d, v in stored.items():
        if id(v) not in by_vec:
            by_vec[id(v)] = similarity(q, v) if len(v) == len(q) else -1000
        if by_vec[id(v)] > 0:
            out[d] = by_vec[id(v)]
    return out


def search(
    conn: Connection,
    user_id: int,
    query: str,
    embedder: Embedder | None = None,
    *,
    limit: int = LIMIT,
    mode: Mode = "hybrid",
) -> list[Hit]:
    """The user's best `limit` documents for `query`. `mode` "fts" and
    "vector" are the evaluation's baselines: one list, in its own order."""
    fts = ix.match(conn, user_id, query, DEPTH) if mode != "vector" else {}
    vec = _top(vector_scores(conn, user_id, query, embedder)) if mode != "fts" else {}
    if mode == "hybrid":
        found = ix.docs(conn, user_id, fts.keys() | vec.keys())
        order = rerank(query, rrf(fts, vec), found)
    else:
        one = fts or vec
        found = ix.docs(conn, user_id, one)
        r = ranks(one)
        order = sorted((d for d in one if d in found), key=lambda d: (r[d], d))
    return [_hit(found[d]) for d in order[:limit]]


def _hit(doc: ix.Doc) -> Hit:
    return Hit(
        doc.id, doc.kind, doc.ref, doc.transaction_id, Snippet(sanitise(doc.body))
    )


def refresh(conn: Connection, user_id: int, embedder: Embedder) -> int | None:
    """Embed every document with no vector from the embedder's model version;
    returns how many, or None when the local model is off. Each distinct body
    is embedded once."""
    model = stored_model(embedder)
    if model is None:
        return None
    todo = ix.pending(conn, user_id, model)
    texts = sorted(set(todo.values()))
    try:
        embedded = dict(zip(texts, embedder.embed(texts), strict=True))
    except EmbeddingUnavailable:
        return None
    return ix.store(conn, user_id, model, {d: embedded[b] for d, b in todo.items()})
