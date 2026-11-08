"""GET /api/search (#279, spec #251 decision 7): the user's own Transactions,
Merchants, Notes, Split parts and Memories by `engine.search` (FTS5 + local
embeddings, RRF, deterministic rerank). Snippets are quoted data
(`{"data": ...}`), sanitised; the query and results are never logged. Read-only:
the local embedding model is used when it is on, otherwise FTS alone."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import Connection

from finance_agent.categorise.embedding import Embedder, OllamaEmbedder
from finance_agent.engine import search
from finance_agent.identity import current_user

router = APIRouter(prefix="/api/search", tags=["search"])


def embedder() -> Embedder:
    return OllamaEmbedder(timeout=2)


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


class SnippetOut(BaseModel):
    data: str


class HitOut(BaseModel):
    kind: str  # transaction | merchant | note | split | memory
    ref: int
    transaction_id: int | None
    snippet: SnippetOut


class SearchOut(BaseModel):
    hits: list[HitOut]


@router.get("")
def find(
    conn: Annotated[Connection, Depends(_conn)],
    emb: Annotated[Embedder, Depends(embedder)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=search.LIMIT)] = search.LIMIT,
) -> SearchOut:
    hits = search.search(conn, current_user(), q, emb, limit=limit)
    return SearchOut(
        hits=[
            HitOut(
                kind=h.kind,
                ref=h.ref,
                transaction_id=h.transaction_id,
                snippet=SnippetOut(data=h.snippet.data),
            )
            for h in hits
        ]
    )
