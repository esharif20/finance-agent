"""Search evaluation (#253, docs/ML_EVALUATIONS.md): recall@5, recall@10 and
MRR, overall and per query type, on the seeded Ledgers of `synthetic_search`,
all built in one throwaway encrypted database (one user each, so every query
also runs beside other users' documents).

    uv run python evals/search.py                # CI: results reproduce
    uv run python evals/search.py --write        # refresh (see gate.py)
    uv run python evals/search.py --ollama       # local opt-in: nomic-embed-text

- **fts**: SQLite FTS5 alone, BM25 order;
- **vector**: embeddings alone, cosine order;
- **hybrid**: `engine.search`, RRF of both plus the deterministic rerank.

CI embeds with `TrigramEmbedder` (string similarity, no meaning), so the
paraphrase scores there are a floor; `--ollama` prints the nomic numbers and
writes nothing. Recall is the share of a query's gold documents in the top k
(a thing's gold is its Transaction and its Merchant); MRR uses the first gold
hit. Latency p95 (ms per hybrid query) is printed, never committed: it is not
reproducible.
"""

import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import keyring
import keyring.backend
from sqlalchemy import Connection

import gate
import synthetic_search as ss
from finance_agent import categorise
from finance_agent.categorise.embedding import Embedder, OllamaEmbedder, TrigramEmbedder
from finance_agent.engine import search
from finance_agent.ledger import Ledger
from finance_agent.ledger import search_index as ix
from finance_agent.ledger.proposals import MemoryDraft, Proposals, Provenance
from finance_agent.storage import Scoped, open_db, schema

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from synthetic_lloyds import History, Txn, render_export  # noqa: E402

RESULTS = Path(__file__).parent / "results" / "search.json"
START = date(2026, 1, 1)
MODES: tuple[search.Mode, ...] = ("fts", "vector", "hybrid")


class _MemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.items.pop((service, username), None)


def _exports(spec: ss.LedgerSpec) -> list[str]:
    """Consecutive Lloyds exports of the rows (an export holds at most 150)."""
    bal, txns = 5_000_000, list[Txn]()
    for i, (desc, minor) in enumerate(spec.rows):
        bal += minor
        txns.append(Txn(START + timedelta(days=i // 2), "DEB", desc, minor, bal))
    h = History("00-00-00", f"0000000{spec.user_id}", txns)
    return [
        render_export(h, i, min(i + 100, len(txns))).text
        for i in range(0, len(txns), 100)
    ]


def build(conn: Connection, spec: ss.LedgerSpec, d: Path) -> list[set[int]]:
    """Write the spec's Ledger; the gold document ids of each query."""
    u = spec.user_id
    paths: list[Path] = []
    for i, csv in enumerate(_exports(spec)):
        paths.append(d / f"export{u}-{i}.csv")
        paths[-1].write_text(csv)
    led = Ledger(conn, u)
    led.import_files(paths)
    categorise.run(conn, u)
    tids = [t.id for t in sorted(led.transactions(), key=lambda t: t.id)]
    db = Scoped(conn, u)
    c = schema.categorisation
    merchant_of = {
        r.transaction_id: r.merchant_id
        for r in db.execute(db.select(c, c.c.transaction_id, c.c.merchant_id))
    }
    n = schema.note
    note_ids: list[int] = []
    for row, words in spec.notes:
        led.set_note(tids[row], words)
        q = db.select(n, n.c.id).where(n.c.transaction_id == tids[row])
        note_ids.append(int(db.execute(q).scalar_one()))
    split_ids = [
        led.split_transaction(
            tids[s.row], [(s.first_minor, s.categories[0]), (None, s.categories[1])]
        ).id
        for s in spec.splits
    ]
    props = Proposals(conn, u)
    said = Provenance(turn_id="t1", quote="synthetic", said_on=START)
    memory_ids: list[int] = []
    for words in spec.memories:
        prop = props.propose_memory(
            MemoryDraft(type="goal", text=words, provenance=said)
        )
        memory_ids.append(int(props.confirm(prop.id).result_id or 0))
    gold: list[set[int]] = []
    for q in spec.queries:
        t = q.target
        if t.kind == "thing":
            tid = tids[t.at]
            g = {ix.doc_id("transaction", tid)}
            if (m := merchant_of.get(tid)) is not None:
                g.add(ix.doc_id("merchant", m))
        elif t.kind == "note":
            g = {ix.doc_id("note", note_ids[t.at])}
        elif t.kind == "split":
            g = {ix.doc_id("split", split_ids[t.at], t.part)}
        else:
            g = {ix.doc_id("memory", memory_ids[t.at])}
        gold.append(g)
    return gold


def _rate(xs: Sequence[float]) -> float:
    return round(sum(xs) / len(xs), 6) if xs else 0.0


def score(runs: Sequence[tuple[ss.Query, set[int], list[int]]]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for label, keep in [("overall", None), *((t, t) for t in ss.QUERY_TYPES)]:
        mine = [(g, got) for q, g, got in runs if keep is None or q.type == keep]
        for k in (5, 10):
            metrics[f"{label}.recall@{k}"] = _rate(
                [len(g & set(got[:k])) / len(g) for g, got in mine]
            )
        metrics[f"{label}.mrr"] = _rate(
            [
                next((1 / r for r, d in enumerate(got, 1) if d in g), 0.0)
                for g, got in mine
            ]
        )
    return metrics


def run(embedder: Embedder) -> tuple[dict[str, dict[str, float]], float]:
    """Metrics per mode, and the hybrid p95 latency in ms."""
    specs = ss.generate()
    prev = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())  # never the real Keychain
    try:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            engine = open_db(d / "db" / "search.db")
            try:
                with engine.begin() as conn:
                    golds = [build(conn, s, d) for s in specs]
                    for s in specs:
                        search.refresh(conn, s.user_id, embedder)
                    out: dict[str, dict[str, float]] = {}
                    ms: list[float] = []
                    for mode in MODES:
                        runs: list[tuple[ss.Query, set[int], list[int]]] = []
                        for s, gold in zip(specs, golds, strict=True):
                            for q, g in zip(s.queries, gold, strict=True):
                                t0 = time.perf_counter()
                                hits = search.search(
                                    conn, s.user_id, q.text, embedder, mode=mode
                                )
                                if mode == "hybrid":
                                    ms.append((time.perf_counter() - t0) * 1000)
                                runs.append((q, g, [h.doc for h in hits]))
                        out[mode] = score(runs)
            finally:
                engine.dispose()
    finally:
        keyring.set_keyring(prev)
    ms.sort()
    return out, ms[min(len(ms) - 1, int(len(ms) * 0.95))]


def evaluate(embedder: Embedder | None = None) -> dict[str, Any]:
    embedder = embedder or TrigramEmbedder()
    specs = ss.generate()
    metrics, p95 = run(embedder)
    print(f"search eval: hybrid latency p95 {p95:.1f} ms", file=sys.stderr)
    return {
        "harness": "search",
        "dataset": "synthetic_search",
        "dataset_version": ss.DATASET_VERSION,
        "dataset_sha256": ss.fingerprint(specs),
        "seed": ss.SEED,
        "ledgers": len(specs),
        "queries": {
            t: sum(q.type == t for s in specs for q in s.queries)
            for t in ss.QUERY_TYPES
        },
        "embedder": embedder.model,
        "k": search.K,
        "models": {
            mode: {
                "model": f"{mode}-{embedder.model}",
                "model_version": "1",
                "metrics": m,
            }
            for mode, m in metrics.items()
        },
    }


def lower_is_better(_: str) -> bool:
    return False


if __name__ == "__main__":
    if "--ollama" in sys.argv:
        import json

        print(json.dumps(evaluate(OllamaEmbedder())["models"], indent=2))
        sys.exit(0)
    sys.exit(gate.run("search", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
