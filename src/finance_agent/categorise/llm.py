"""The merchant-level local LLM classifier (#215 F, ADR 0005 as amended).

One call per Merchant, not per row, to the local Ollama chat model (the #144
pick, the Assistant's `OllamaChatModel` settings: think off, temperature 0).
Its reply is JSON constrained to the taxonomy's sub-Categories or "unsure"
(Ollama's `format` schema), so it can only name a Category, never a figure.
It sees the payee, a few description tokens, amount *patterns in words*
computed here (median band, recurrence, weekday spread; never an amount) and
the k nearest labelled Merchants as few-shot examples: the user's Corrections
and the UK merchant base, found by the existing embedder. Inference only; no
weights change (ADR 0015).

Transport is the embedder's (`OllamaEmbedder`): localhost:11434 only, no
redirects, no proxies, a bounded reply. Descriptions go to localhost and
nowhere else; logs carry counts only. The answer is cached per Merchant and
model version in `category_prediction` (model "llm:<name>@<digest>", the
Uncategorised id with score 0 for "unsure"), so a Merchant is asked once.
`refresh_quietly` runs after a sync, in a background thread, then re-runs
the agreement gate (`auto.apply`).
"""

import json
import logging
import statistics
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, Engine

from finance_agent.categorise import embedding
from finance_agent.categorise.embedding import (
    EmbeddingUnavailable,
    OllamaEmbedder,
    _Connection,  # pyright: ignore[reportPrivateUsage]
)
from finance_agent.ledger import CATEGORISED_KINDS, Ledger
from finance_agent.ledger.taxonomy import TAXONOMY, UNCATEGORISED
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

MODEL = (
    "qwen3.5:4b-q4_K_M"  # about 4.7 GB peak; the 9b Assistant pick swaps the 16 GB Mac
)
PREFIX = "llm:"
KEEP_ALIVE = "1m"  # unload soon: the Mac is shared with the Assistant
NUM_CTX = 4096  # a few-shot prompt is well under 1k tokens
PROMPT_VERSION = 1  # bump when the prompt changes; the eval cache records it
K = 5  # few-shot neighbours
DESCRIPTIONS = 3
UNSURE = "unsure"
ANSWERED = embedding.SIMILARITY  # the cache row's score when it named one

type Category = tuple[str, str]


def label(c: Category) -> str:
    return f"{c[0]} › {c[1]}"


LABELS: dict[str, Category] = {
    label((top, sub)): (top, sub)
    for top, subs in TAXONOMY.items()
    if top != UNCATEGORISED
    for sub in subs
}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"category": {"type": "string", "enum": [*LABELS, UNSURE]}},
    "required": ["category"],
}
SYSTEM = (
    "You file one UK card payee into exactly one spending sub-category. "
    "The bank gives only the payee text: no merchant code, no web. "
    "Similar payees the user already filed are the strongest evidence. "
    'Reply with JSON {"category": "<one allowed id>"}, or {"category": '
    '"unsure"} when the name gives no real clue. Allowed ids: ' + "; ".join(LABELS)
)


@dataclass(frozen=True)
class Payee:
    """What the model is told about one Merchant."""

    name: str
    descriptions: tuple[str, ...]
    payments: tuple[tuple[date, Decimal], ...]  # (posted on, signed amount)
    examples: tuple[tuple[str, Category], ...]  # nearest labelled first


_BANDS = ((Decimal(5), "under £5"), (Decimal(20), "£5 to £20"),
          (Decimal(60), "£20 to £60"), (Decimal(200), "£60 to £200"))  # fmt: skip


def pattern(payments: Sequence[tuple[date, Decimal]]) -> str:
    """Amount and timing as words: the median band, the cadence and the
    weekday spread. No amount leaves as a number."""
    if not payments:
        return "no payments"
    median = statistics.median(abs(a) for _, a in payments)
    band = next((w for top, w in _BANDS if median < top), "over £200")
    days = sorted(d for d, _ in payments)
    if len(days) == 1:
        cadence = "one payment"
    else:
        gaps = [(b - a).days for a, b in zip(days, days[1:], strict=False)]
        gap = statistics.median(gaps)
        regular = len(days) >= 3
        cadence = (
            "weekly" if regular and 5 <= gap <= 9
            else "monthly" if regular and 25 <= gap <= 35
            else "yearly" if 350 <= gap <= 380
            else f"{len(days)} payments, irregular"
        )  # fmt: skip
    weekend = sum(d.weekday() >= 5 for d in days) * 100 // len(days)
    spread = (
        "mostly weekends" if weekend >= 70
        else "weekdays only" if weekend == 0 and len(days) >= 3
        else "any day"
    )  # fmt: skip
    return f"typical payment {band}; {cadence}; {spread}"


def messages(p: Payee) -> list[dict[str, str]]:
    shots = "\n".join(f"- {n} -> {label(c)}" for n, c in p.examples) or "- none"
    user = (
        f"Payee: {p.name}\n"
        f"Descriptions: {' | '.join(p.descriptions[:DESCRIPTIONS])}\n"
        f"Pattern: {pattern(p.payments)}\n"
        f"Similar payees the user has filed:\n{shots}"
    )
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse(content: Any) -> Category | None:
    """The Category a reply names; None for "unsure" or anything else."""
    try:
        obj = json.loads(content) if isinstance(content, str) else None
    except (ValueError, RecursionError):
        return None
    got = embedding._field(obj, "category")  # pyright: ignore[reportPrivateUsage]
    return LABELS.get(got) if isinstance(got, str) else None


class OllamaClassifier(OllamaEmbedder):
    """The chat side of the embedder's pinned localhost transport."""

    def __init__(
        self,
        model: str = MODEL,
        base_url: str = embedding.OLLAMA_URL,
        *,
        keep_alive: str = KEEP_ALIVE,
        timeout: int = 120,
        connect: Callable[[], _Connection] | None = None,
    ) -> None:
        super().__init__(
            model, base_url, keep_alive=keep_alive, timeout=timeout, connect=connect
        )

    def classify(self, p: Payee) -> Category | None:
        """Raises EmbeddingUnavailable when Ollama is down or off."""
        reply = self._call(
            "POST",
            "/api/chat",
            {
                "model": self.model,
                "messages": messages(p),
                "stream": False,
                "think": False,  # qwen3.5 thinks by default: 10x slower (#144)
                "format": SCHEMA,
                "keep_alive": self._keep_alive,
                "options": {
                    "temperature": 0,
                    "presence_penalty": 0,
                    "num_ctx": NUM_CTX,
                },
            },
        )
        message = embedding._field(reply, "message")  # pyright: ignore[reportPrivateUsage]
        content = embedding._field(message, "content")  # pyright: ignore[reportPrivateUsage]
        return parse(content)


def stored_model(c: OllamaClassifier) -> str | None:
    """The cache's model column: "llm:<name>@<digest[:6]>" (short, so the
    gate's combined version fits the 64-character source column)."""
    digest = c.digest()
    if digest is None:
        return None
    return f"{PREFIX}{c.model}@{digest[:6]}" if digest else PREFIX + c.model


# --- the app side --------------------------------------------------------------


def labelled(conn: Connection, user_id: int) -> dict[int, set[Category]]:
    """Merchant id -> Categories the user stands behind: active Merchant-wide
    Corrections (so an override of an `auto-low` row becomes a few-shot
    example at once, #215 G) and Merchants with a base default. Never an
    auto row."""
    led = Ledger(conn, user_id)
    out: dict[int, set[Category]] = {}
    for m in led.merchants():
        if m.default_category and m.default_category[0] != UNCATEGORISED:
            out.setdefault(m.id, set()).add(m.default_category)
    corrected: dict[int, Category] = {}
    for k in led.corrections():  # oldest first: the latest wins
        if k.merchant_id is not None and k.reverted_at is None:
            corrected[k.merchant_id] = k.category
    for m, c in corrected.items():
        if c[0] != UNCATEGORISED:
            out[m] = {c}  # the user's answer beats the base default
    return out


def _pending(conn: Connection, user_id: int, model: str) -> dict[int, Payee]:
    """Uncategorised Merchants the model has not answered yet, with what it
    is told about each."""
    db = Scoped(conn, user_id)
    p = schema.category_prediction
    asked = set(
        db.execute(db.select(p, p.c.merchant_id).where(p.c.model == model)).scalars()
    )
    rows: dict[int, list[tuple[date, Decimal, str]]] = {}
    names: dict[int, str] = {}
    for t in Ledger(conn, user_id).transactions():
        c = t.categorisation
        if (
            c is None
            or c.merchant_id is None
            or c.kind not in CATEGORISED_KINDS
            or c.category != (UNCATEGORISED, UNCATEGORISED)
            or c.merchant_id in asked
        ):
            continue
        rows.setdefault(c.merchant_id, []).append(
            (t.posted_on, t.amount, t.description)
        )
        names[c.merchant_id] = c.merchant or ""
    if not rows:
        return {}
    known = labelled(conn, user_id)
    vecs = embedding.vectors(conn, user_id, _vector_model(conn, user_id) or "")
    merchant_names = {m.id: m.name for m in Ledger(conn, user_id).merchants()}
    out: dict[int, Payee] = {}
    for m, rs in rows.items():
        shots: list[tuple[str, Category]] = []
        if m in vecs:
            near = embedding.neighbours(
                vecs[m],
                [(vecs[n], n) for n in sorted(known) if n in vecs and n != m],
                K,
            )
            shots = [(merchant_names[n], min(known[n])) for _, n in near]
        out[m] = Payee(
            name=names[m],
            descriptions=tuple(dict.fromkeys(d for _, _, d in rs)),
            payments=tuple((d, a) for d, a, _ in rs),
            examples=tuple(shots),
        )
    return out


def _vector_model(conn: Connection, user_id: int) -> str | None:
    """The stored vectors' model version covering most Merchants."""
    db = Scoped(conn, user_id)
    me = schema.merchant_embedding
    counts: dict[str, int] = {}
    for model in db.execute(db.select(me, me.c.model)).scalars():
        counts[model] = counts.get(model, 0) + 1
    return max(counts, key=lambda k: (counts[k], k)) if counts else None


def _store(
    conn: Connection, user_id: int, model: str, answers: Mapping[int, Category | None]
) -> None:
    from finance_agent.categorise import seed_taxonomy  # the package imports us

    db = Scoped(conn, user_id)
    p, m = schema.category_prediction, schema.merchant
    ids = seed_taxonomy(conn, user_id)
    alive = set(
        db.execute(db.select(m, m.c.id).where(m.c.id.in_(list(answers)))).scalars()
    )
    done = set(
        db.execute(
            db.select(p, p.c.merchant_id).where(
                p.c.model == model, p.c.merchant_id.in_(list(answers))
            )
        ).scalars()
    )
    for merchant, cat in answers.items():
        if merchant not in alive or merchant in done:
            continue  # deleted meanwhile, or another pass answered it
        db.insert(
            p, model=model, merchant_id=merchant,
            category_id=ids[cat if cat in ids else (UNCATEGORISED, UNCATEGORISED)],
            score=ANSWERED if cat in ids else 0, created_at=datetime.now(UTC),
        )  # fmt: skip


def refresh(engine: Engine, user_id: int, classifier: OllamaClassifier) -> int | None:
    """Ask the model about every Merchant it has not answered yet, holding no
    transaction during the calls; returns how many, None when it is off."""
    model = stored_model(classifier)
    if model is None:
        log.info("llm local model off")
        return None
    with engine.begin() as conn:
        todo = _pending(conn, user_id, model)
    answers: dict[int, Category | None] = {}
    try:
        for i, m in enumerate(sorted(todo), 1):
            answers[m] = classifier.classify(todo[m])
            if i % 25 == 0:  # keep progress if the model goes away mid-run
                with engine.begin() as conn:
                    _store(conn, user_id, model, answers)
    except EmbeddingUnavailable:
        log.info("llm local model off")
    with engine.begin() as conn:
        _store(conn, user_id, model, answers)
    log.info(
        "llm merchants=%d answered=%d",
        len(answers),
        sum(1 for a in answers.values() if a),
    )
    return len(answers)


_running = threading.Lock()


def refresh_quietly(engine: Engine, user_id: int) -> None:
    """`refresh` with the real local model, then the gate with every guess
    filed (`auto.apply(final=True)`). Never raises; one run at a time."""
    from finance_agent.categorise import auto  # auto imports us

    if not _running.acquire(blocking=False):
        return
    try:
        refresh(engine, user_id, OllamaClassifier())
        with engine.begin() as conn:
            auto.apply(conn, user_id, auto.ids(conn, user_id), final=True)
    except Exception as e:  # noqa: BLE001
        log.warning("llm refresh failed: %s", type(e).__name__)
    finally:
        _running.release()


def start(engine: Engine, user_id: int) -> threading.Thread:
    """Run `refresh_quietly` in the background after a sync (575 Merchants
    take minutes on a laptop); the sync itself returns at once. Not a daemon:
    a one-shot `fa sync` waits for it before exiting, so the low tier is filed;
    an interrupted run keeps its answers (stored every 25)."""
    t = threading.Thread(
        target=refresh_quietly, args=(engine, user_id), name="llm-categorise"
    )
    t.start()
    return t
