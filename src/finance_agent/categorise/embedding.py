"""Merchant embeddings (#113): nomic-embed-text through local Ollama, used for
inference only (ADR 0009, 0015). No weights are trained.

`OllamaEmbedder` talks to http://localhost:11434 only, over `http.client` like
the Assistant's `OllamaChatModel` (THREAT_MODEL B4): no redirects, no proxy env
vars, so the host it dials is the one checked here. Ollama loads the model on
the first request and unloads it about a minute after the last (`keep_alive`).
When Ollama is down, not installed or the model is not pulled, everything
raises `EmbeddingUnavailable`, callers keep the rules, and `/api/local-model`
says "off". The app never downloads a model: `ollama pull` is the user's step.

Vectors are integers: each component scaled so the largest is +-32767 and
stored as int16, so no float reaches `categorise` (tests/test_no_float.py) and
similarity is exact. One vector per Merchant, user-scoped, in
`merchant_embedding`; it goes when its Merchant is deleted (foreign key) or the
last Transaction naming it is (a trigger, migration 0014), and
`fa forget-embeddings` wipes them all. Errors and logs never carry descriptors,
Merchant names or vectors.
"""

import hashlib
import http.client
import json
import logging
import math
from array import array
from collections.abc import Callable, Mapping, Sequence
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from sqlalchemy import Connection

from finance_agent.ledger import Ledger
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

MODEL = "nomic-embed-text"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_PORT = 11434
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
KEEP_ALIVE = "1m"
SCALE = 32767  # int16
MAX_DIMS = 4096
SIMILARITY = 1000  # similarity() is cosine in thousandths

type Vector = tuple[int, ...]


class OllamaHostRefused(ValueError):
    pass


class EmbeddingUnavailable(RuntimeError):
    """Ollama is down, not installed, lacks the model or replied badly."""


class Embedder(Protocol):
    model: str

    def embed(self, texts: Sequence[str]) -> list[Vector]: ...


class _Response(Protocol):
    status: int

    def read(self) -> bytes: ...
    def close(self) -> None: ...


class _Connection(Protocol):
    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Mapping[str, str] = ...,
    ) -> None: ...
    def getresponse(self) -> _Response: ...
    def close(self) -> None: ...


def _local_endpoint(base_url: str) -> tuple[str, int]:
    """Mirrors the Assistant's guard (assistant/ollama.py)."""
    refused = OllamaHostRefused("embeddings may only use http://localhost:11434")
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:
        raise refused from None
    if (
        parts.scheme != "http"
        or parts.hostname not in LOCAL_HOSTS
        or port != OLLAMA_PORT
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise refused
    return parts.hostname, port


def quantise(values: Sequence[Decimal | int]) -> Vector:
    """Scale so the largest component is +-SCALE; cosine is scale-free."""
    top = max((abs(v) for v in values), default=0)
    if not top:
        return tuple(0 for _ in values)
    return tuple(
        int((Decimal(v) * SCALE / top).to_integral_value(ROUND_HALF_EVEN))
        for v in values
    )


def similarity(a: Vector, b: Vector) -> int:
    """Cosine similarity in thousandths (-1000..1000); 0 for a zero vector."""
    if len(a) != len(b):
        raise ValueError("vectors of different lengths")
    norms = math.isqrt(sum(x * x for x in a) * sum(y * y for y in b))
    if not norms:
        return 0
    cos = sum(x * y for x, y in zip(a, b, strict=True)) * SIMILARITY // norms
    return max(-SIMILARITY, min(SIMILARITY, cos))


def _field(obj: Any, key: str) -> Any:
    return cast(dict[str, Any], obj).get(key) if isinstance(obj, dict) else None


def _items(obj: Any) -> list[Any]:
    """A JSON array, or [] for anything else (a malformed reply)."""
    return cast(list[Any], obj) if isinstance(obj, list) else []


def _number(v: Any) -> bool:
    return (
        isinstance(v, int | Decimal)
        and not isinstance(v, bool)
        and Decimal(v).is_finite()
    )


class OllamaEmbedder:
    def __init__(
        self,
        model: str = MODEL,
        base_url: str = OLLAMA_URL,
        *,
        keep_alive: str = KEEP_ALIVE,
        timeout: int = 30,
        connect: Callable[[], _Connection] | None = None,
    ) -> None:
        self.model = model
        self._host, self._port = _local_endpoint(base_url)
        self._keep_alive = keep_alive
        self._timeout = timeout
        self._factory: Callable[[], _Connection] = connect or self.open_connection

    def open_connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)

    def _call(self, method: str, path: str, body: Any = None) -> Any:
        conn = self._factory()
        try:
            try:
                conn.request(
                    method,
                    path,
                    None if body is None else json.dumps(body).encode(),
                    {"Content-Type": "application/json"},
                )
                resp = conn.getresponse()
                raw = resp.read()
            except (OSError, http.client.HTTPException):
                raise EmbeddingUnavailable(
                    "Ollama is not running on localhost"
                ) from None
            if resp.status != 200:  # a redirect is refused, never followed
                raise EmbeddingUnavailable(f"Ollama returned HTTP {resp.status}")
            try:
                return json.loads(raw, parse_float=Decimal)
            except ValueError:
                raise EmbeddingUnavailable("malformed reply from Ollama") from None
        finally:
            conn.close()

    def digest(self) -> str | None:
        """The pulled model's digest (its version), or None when it is not
        pulled or Ollama is down. Lists models only; loads nothing."""
        try:
            tags = self._call("GET", "/api/tags")
        except EmbeddingUnavailable:
            return None
        for m in _items(_field(tags, "models")):
            name, digest = _field(m, "name"), _field(m, "digest")
            if name in (self.model, f"{self.model}:latest") and isinstance(digest, str):
                return digest
        return None

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        reply = self._call(
            "POST",
            "/api/embed",
            {"model": self.model, "input": list(texts), "keep_alive": self._keep_alive},
        )
        vectors = [_items(v) for v in _items(_field(reply, "embeddings"))]
        if (
            len(vectors) != len(texts)
            or not all(0 < len(v) <= MAX_DIMS and all(map(_number, v)) for v in vectors)
            or len({len(v) for v in vectors}) != 1
        ):
            raise EmbeddingUnavailable("malformed embeddings from Ollama")
        return [quantise(v) for v in vectors]


class TrigramEmbedder:
    """Hashed character trigrams: a deterministic stand-in for tests and the CI
    evaluation, so neither needs Ollama. String similarity, not meaning."""

    model = "hashed-trigrams-v1"
    dims = 256

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        out: list[Vector] = []
        for text in texts:
            padded = f"  {text.upper()} "
            v = [0] * self.dims
            for i in range(len(padded) - 2):
                h = hashlib.blake2b(padded[i : i + 3].encode(), digest_size=4)
                v[int.from_bytes(h.digest()) % self.dims] += 1
            out.append(quantise(v))
        return out


# --- storage ----------------------------------------------------------------


# shortcut: native byte order (little-endian on every Mac the app runs on);
# pin it if the database ever moves between architectures.
def _pack(v: Vector) -> bytes:
    return array("h", v).tobytes()


def _unpack(blob: bytes) -> Vector:
    a = array("h")
    a.frombytes(blob)
    return tuple(a)


def refresh(conn: Connection, user_id: int, embedder: Embedder) -> int | None:
    """Embed every categorised Merchant that has no vector from `embedder`'s
    model yet; returns how many, or None when the local model is off (the
    rules carry on alone). Drops vectors whose Merchant no longer names any
    Transaction. Call after `categorise.run`."""
    db = Scoped(conn, user_id)
    me, cat = schema.merchant_embedding, schema.categorisation
    named = db.select(cat, cat.c.merchant_id).where(cat.c.merchant_id.is_not(None))
    db.execute(db.delete(me).where(me.c.merchant_id.not_in(named)))
    have = set(
        db.execute(
            db.select(me, me.c.merchant_id).where(me.c.model == embedder.model)
        ).scalars()
    )
    wanted = set(db.execute(named.distinct()).scalars()) - have
    names = {m.id: m.name for m in Ledger(conn, user_id).merchants() if m.id in wanted}
    ids = sorted(names)
    try:
        vectors = embedder.embed([names[i] for i in ids])
    except EmbeddingUnavailable:
        log.info("embeddings local model off")
        return None
    db.execute(db.delete(me).where(me.c.merchant_id.in_(ids)))  # another model's
    for i, v in zip(ids, vectors, strict=True):
        db.insert(me, merchant_id=i, model=embedder.model, dims=len(v), vector=_pack(v))
    log.info("embeddings merchants=%d", len(ids))
    return len(ids)


def vectors(conn: Connection, user_id: int, model: str) -> dict[int, Vector]:
    """This user's stored vectors from `model`, by Merchant id."""
    db = Scoped(conn, user_id)
    me = schema.merchant_embedding
    rows = db.execute(
        db.select(me, me.c.merchant_id, me.c.vector).where(me.c.model == model)
    )
    return {r.merchant_id: _unpack(r.vector) for r in rows}


def forget(conn: Connection, user_id: int) -> int:
    """Delete every stored vector of this user; returns how many. They are
    derived data, rebuilt by the next `refresh`."""
    db = Scoped(conn, user_id)
    n = db.execute(db.delete(schema.merchant_embedding)).rowcount
    log.info("embeddings forgotten=%d", n)
    return n
