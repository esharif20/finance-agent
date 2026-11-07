"""`/api/local-model` (#113): whether the local embedding model is on, so the
Review Queue can say "local model off". It only lists Ollama's models on
localhost; it never loads or downloads one."""

from typing import Annotated, Literal, Protocol

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from finance_agent.categorise.embedding import OllamaEmbedder

router = APIRouter(prefix="/api/local-model", tags=["local-model"])


class Probe(Protocol):
    def digest(self) -> str | None: ...


def probe() -> Probe:
    return OllamaEmbedder(timeout=2)


class LocalModelOut(BaseModel):
    status: Literal["on", "off"]  # off: Ollama down, not installed or not pulled


@router.get("")
def local_model(p: Annotated[Probe, Depends(probe)]) -> LocalModelOut:
    return LocalModelOut(status="off" if p.digest() is None else "on")
