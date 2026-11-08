"""Semantic resolution of Category and Merchant terms (#317).

Two jobs, both on the vectors sync already makes (ADR 0009: no training):

- A Category the resolver read ("fuel") also counts the Merchants filed
  elsewhere whose name sits nearest that Category's description, by a
  calibrated threshold and margin: "plus Esso, filed under Shopping".
- A term no resolver read ("forecourt") goes to its nearest Category
  description when that one is near enough and clear of the runner-up, and
  to the Merchants its vector and FTS5 BM25 both rank, merged by RRF.

The index (normalised float32 matrices of Merchant and description vectors)
is built at sync and when `fa` starts, and held in memory per user and Ledger
version; a lookup is one matrix product. Query vectors are cached. With
Ollama off, or a model with no calibrated thresholds, nothing is added.
Thresholds come from evals/semantic_resolution.py; a test keeps them equal.
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from sqlalchemy import Connection, Engine

from finance_agent.categorise import embedding, zero_shot
from finance_agent.categorise.embedding import Embedder, EmbeddingUnavailable
from finance_agent.ledger import Ledger
from finance_agent.ledger import search_index as fts

log = logging.getLogger(__name__)  # counts only, never a term or a name

type Category = tuple[str, str]
RRF_K = 60  # the usual constant: a rank's weight is 1 / (RRF_K + rank)
TOP = 8  # candidates per ranking
CACHE = 256  # query vectors held


@dataclass(frozen=True)
class Thresholds:
    """Similarities in thousandths (embedding.similarity)."""

    filed: int  # a Merchant counts for a Category filed elsewhere: at least this
    filed_margin: int  # ... and this far past its next-nearest description
    term: int  # a term names a Category: at least this
    term_margin: int  # ... and this far past the runner-up
    merchant: int  # a term's vector names a Merchant: at least this


# model name -> thresholds; a model with none adds nothing
CALIBRATED: dict[str, Thresholds] = {
    "nomic-embed-text": Thresholds(
        filed=450, filed_margin=45, term=450, term_margin=70, merchant=590
    ),
}


def nearest(sims: Sequence[int], cats: Sequence[Category]) -> tuple[int, int, Category]:
    """(best similarity, its margin over the runner-up, its Category)."""
    order = sorted(range(len(cats)), key=lambda i: (-sims[i], cats[i]))
    best = order[0]
    second = sims[order[1]] if len(order) > 1 else -embedding.SIMILARITY
    return sims[best], sims[best] - second, cats[best]


def counts_for(
    sims: Sequence[int],
    cats: Sequence[Category],
    target: Category,
    own: Category | None,
    th: Thresholds,
) -> bool:
    """Does a Merchant filed under `own` belong to `target` too?"""
    if own == target:
        return False
    sim, margin, cat = nearest(sims, cats)
    return cat == target and sim >= th.filed and margin >= th.filed_margin


def term_category(
    sims: Sequence[int], cats: Sequence[Category], th: Thresholds
) -> Category | None:
    sim, margin, cat = nearest(sims, cats)
    return cat if sim >= th.term and margin >= th.term_margin else None


def rrf(*rankings: Sequence[int]) -> list[int]:
    """Reciprocal rank fusion: ids by summed 1 / (RRF_K + rank), ties by id."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for rank, i in enumerate(ranking, 1):
            score[i] = score.get(i, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(score, key=lambda i: (-score[i], i))


def _unit(rows: list[embedding.Vector]) -> np.ndarray:
    m = np.asarray(rows, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1, n)


def _thousandths(m: np.ndarray) -> np.ndarray:
    return np.floor(np.clip(m, -1, 1) * embedding.SIMILARITY).astype(np.int64)


@dataclass
class Index:
    model: str  # the stored model version of the vectors
    version: int  # the Ledger version it was built at
    ids: list[int]  # Merchant ids, the rows of `merchants`
    names: dict[int, str]
    own: dict[int, Category | None]  # each Merchant's default Category
    merchants: np.ndarray  # unit rows
    cats: list[Category]
    labels: np.ndarray  # unit rows, one per description
    by_label: np.ndarray  # Merchant x description similarities (thousandths)

    def filed_elsewhere(
        self, target: Category, th: Thresholds
    ) -> list[tuple[int, str, Category | None]]:
        """(id, name, filed under) for each Merchant counted for `target`."""
        if target not in self.cats:
            return []
        return [
            (mid, self.names[mid], self.own[mid])
            for r, mid in enumerate(self.ids)
            if counts_for(
                self.by_label[r].tolist(), self.cats, target, self.own[mid], th
            )
        ]


_index: dict[int, Index] = {}
_queries: dict[tuple[str, str], np.ndarray] = {}
_lock = threading.Lock()
EMBEDDER: Callable[[], Embedder] = embedding.OllamaEmbedder


def thresholds(model: str) -> Thresholds | None:
    return CALIBRATED.get(model.split("@")[0])


def build(conn: Connection, user_id: int) -> Index | None:
    """The index from stored vectors and cached labels; None without either."""
    found = embedding.stored_vectors(conn, user_id)
    if found is None:
        return None
    model, vecs = found
    labels = zero_shot.labels(model)
    if labels is None:
        return None
    led = Ledger(conn, user_id)
    known = {m.id: m for m in led.merchants()}
    ids = sorted(
        i
        for i in vecs
        if i in known and len(vecs[i]) == len(next(iter(labels.values())))
    )
    if not ids:
        return None
    cats = sorted(labels)
    mm, lm = _unit([vecs[i] for i in ids]), _unit([labels[c] for c in cats])
    idx = Index(
        model=model,
        version=led.ledger_version(),
        ids=ids,
        names={i: known[i].name for i in ids},
        own={i: known[i].default_category for i in ids},
        merchants=mm,
        cats=cats,
        labels=lm,
        by_label=_thousandths(mm @ lm.T),
    )
    with _lock:
        _index[user_id] = idx
    log.info("semantic index merchants=%d labels=%d", len(ids), len(cats))
    return idx


def index(led: Ledger) -> Index | None:
    """This user's index while the Ledger is unchanged, else rebuilt (no
    Ollama call: the labels must already be cached)."""
    hit = _index.get(led.user_id)
    if hit is not None and hit.version == led.ledger_version():
        return hit
    return build(led.conn, led.user_id)


def refresh_quietly(engine: Engine, user_id: int) -> None:
    """Prime the description vectors, then build; never raises."""
    try:
        e = EMBEDDER()
        if (model := embedding.stored_model(e)) is not None:
            zero_shot.prime(model, e)
        with engine.connect() as conn:
            build(conn, user_id)
    except Exception as err:  # noqa: BLE001
        log.warning("semantic index failed: %s", type(err).__name__)


def start(engine: Engine, user_id: int) -> threading.Thread:
    t = threading.Thread(
        target=refresh_quietly, args=(engine, user_id), name="semantic", daemon=True
    )
    t.start()
    return t


def _query(model: str, term: str) -> np.ndarray | None:
    key = (model, term.casefold())
    if (hit := _queries.get(key)) is not None:
        return hit
    try:
        [v] = EMBEDDER().embed([term])
    except (EmbeddingUnavailable, ValueError):
        return None
    u = _unit([v])[0]
    with _lock:
        if len(_queries) >= CACHE:
            _queries.pop(next(iter(_queries)))
        _queries[key] = u
    return u


@dataclass(frozen=True)
class Found:
    category: Category | None
    merchant_ids: tuple[int, ...]


def term(led: Ledger, phrase: str) -> Found | None:
    """A phrase no resolver read: its nearest Category description, and the
    Merchants its vector and FTS5 both rank (RRF). None when nothing fits."""
    idx = index(led)
    th = idx and thresholds(idx.model)
    if idx is None or th is None:
        return None
    q = _query(idx.model, phrase)
    if q is None or len(q) != idx.labels.shape[1]:
        return None
    cat = term_category(_thousandths(idx.labels @ q).tolist(), idx.cats, th)
    sims = _thousandths(idx.merchants @ q)
    scores: list[int] = sims.tolist()
    order = sorted(range(len(scores)), key=lambda r: (-scores[r], r))[:TOP]
    by_vec = [idx.ids[r] for r in order if scores[r] >= th.merchant]
    hits = fts.match(led.conn, led.user_id, phrase, 50)
    by_fts = [
        ref
        for doc in hits
        if (kind_ref := fts.kind_of(doc))[0] == "merchant"
        and (ref := kind_ref[1]) in idx.names
    ][:TOP]
    ids = tuple(rrf(by_vec, by_fts)[:TOP])
    if cat is None and not ids:
        return None
    return Found(cat, ids)
