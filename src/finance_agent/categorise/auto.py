"""Confident auto-apply right after sync (#215 B, ADR 0005 as amended).

After rules and the UK merchant base, a Category Review item's embedding
guess is applied as source `auto:<model version>` when its score is at or
above THRESHOLDS[model name], the lowest similarity that keeps >= 95%
precision on the synthetic #112 eval (evals/results/categoriser_*.json,
"calibration"). A model without a calibrated threshold never auto-applies.

Every guess is logged per model version (`embedding.stored_model`: name and
digest), and the user's Merchant-wide answer is recorded against it: a live
accuracy record that measures and never blocks, except that a version whose
agreement falls below PRECISION% over MIN_DECISIONS answers is suspended.

Auto rows are derived like every other categorisation, re-applied from the
log on each `categorise.run`; `undo` takes back the latest batch (one sync)
for good. Corrections are an overlay on read, so they always win.
`category_prediction` holds ids, Category ids, scores and the version only.
"""

import logging
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, func, or_

from finance_agent.categorise import embedding, llm
from finance_agent.ledger import CATEGORISED_KINDS
from finance_agent.ledger import UNCATEGORISED as _NONE
from finance_agent.ledger.review import CategoryItems, ReviewQueue
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

# model name -> threshold in similarity thousandths, copied from the committed
# synthetic calibration (a test keeps them equal)
THRESHOLDS: dict[str, int] = {
    "nomic-embed-text": 720,
    "hashed-trigrams-v1": 611,
    "nomic-embed-text+qwen3.5:4b-q4_K_M": 681,  # kNN and LLM agree
}
MIN_DECISIONS = 50
PRECISION = 95  # percent
SOURCE = "auto:"
LOW = "auto-low:"  # a best guess the gate is not sure of (#215 F)
LOW_MODEL = "low:"  # its log rows: "low:<the version that guessed>"
REVIEW_CAP = 10  # at most this many Merchants per sync are asked about
# The low tier, from the committed synthetic gate results (a test keeps them
# equal): which guess to file on a disagreement, and how often each kind of
# low guess is right (percent), for spend x (1 - confidence).
LOW_PREFERS = "llm"
LOW_CONFIDENCE: dict[str, int] = {"agree": 64, "llm": 57}  # no kNN-only low guess: 0

type Category = tuple[str, str]
UNCATEGORISED = (_NONE, _NONE)


def _ids(conn: Connection, user_id: int) -> dict[Category, int]:
    from finance_agent.categorise import seed_taxonomy  # the package imports us

    return seed_taxonomy(conn, user_id)


def _suspended(db: Scoped, model: str, threshold: int) -> bool:
    """The live accuracy monitor (#215 G): once the user has answered at least
    MIN_DECISIONS of `model`'s guesses at or above the threshold and fewer
    than PRECISION% agree, the version stops auto-applying, for good. It only
    measures until then; it never holds anything back."""
    p, ms = schema.category_prediction, schema.model_suspension
    if db.execute(db.select(ms, ms.c.id).where(ms.c.model == model)).first():
        return True
    judged = db.execute(
        db.select(p, p.c.category_id, p.c.decided_category_id).where(
            p.c.model == model,
            p.c.decided_category_id.is_not(None),
            p.c.score >= threshold,
        )
    ).all()
    hits = sum(r.category_id == r.decided_category_id for r in judged)
    if len(judged) < MIN_DECISIONS or hits * 100 >= PRECISION * len(judged):
        return False
    db.insert(ms, model=model, decisions=len(judged), suspended_at=datetime.now(UTC))
    log.info("auto-apply suspended decisions=%d", len(judged))
    return True


def _set(db: Scoped, merchant_id: int, category_id: int, source: str) -> int:
    """The Merchant's still-Uncategorised rows take `category_id`."""
    c = schema.categorisation
    return db.execute(
        db.update(c)
        .where(
            c.c.merchant_id == merchant_id,
            c.c.source == "default",
            c.c.kind.in_(CATEGORISED_KINDS),
        )
        .values(category_id=category_id, source=source)
    ).rowcount


def _record_decisions(db: Scoped) -> None:
    """A logged guess's Merchant now has a Merchant-wide Correction: that is
    the user's answer to the item, or their override of an auto row."""
    p, k = schema.category_prediction, schema.correction
    answers = {
        r.merchant_id: r.category_id
        for r in db.execute(
            db.select(k, k.c.merchant_id, k.c.category_id)
            .where(
                k.c.merchant_id.is_not(None),
                k.c.reverted_at.is_(None),
                k.c.kind == "category",
            )
            .order_by(k.c.id)  # the latest wins
        )
    }
    logged = db.execute(
        db.select(p, p.c.id, p.c.merchant_id, p.c.decided_category_id).where(
            p.c.merchant_id.in_(answers)
        )
    ).all()
    for r in logged:  # the user's latest answer is the one that counts
        if r.decided_category_id != answers[r.merchant_id]:
            db.execute(
                db.update(p)
                .where(p.c.id == r.id)
                .values(decided_category_id=answers[r.merchant_id])
            )


def _name(version: str) -> str:
    """A version without its digests: "nomic-embed-text+qwen3.5:4b-q4_K_M"."""
    return "+".join(part.split("@")[0] for part in version.split("+"))


def threshold(version: str) -> int | None:
    return THRESHOLDS.get(_name(version))


def ids(conn: Connection, user_id: int) -> dict[Category, int]:
    """This user's current Category ids."""
    return _ids(conn, user_id)


def _answers(
    db: Scoped, ids: Mapping[Category, int]
) -> tuple[str | None, dict[int, Category]]:
    """The LLM version with the most cached answers, and its named Categories
    by Merchant ("unsure" rows carry the Uncategorised id and are left out)."""
    p = schema.category_prediction
    rows = db.execute(
        db.select(p, p.c.model, p.c.merchant_id, p.c.category_id).where(
            p.c.model.startswith(llm.PREFIX)
        )
    ).all()
    if not rows:
        return None, {}
    counts = Counter(r.model for r in rows)
    version = max(counts, key=lambda m: (counts[m], m))
    name = {i: c for c, i in ids.items()}
    return version, {
        r.merchant_id: name[r.category_id]
        for r in rows
        if r.model == version
        and r.category_id in name
        and name[r.category_id] != UNCATEGORISED
    }


def apply(
    conn: Connection, user_id: int, ids: dict[Category, int], *, final: bool = False
) -> int:
    """Re-apply live auto rows, then decide each Category Review item (#215 F):

    - confident (`auto:`): the kNN alone at its threshold, or the kNN and the
      LLM agreeing at the agreement threshold, or the LLM alone where it has
      a calibrated threshold of its own;
    - `final` only (the pass after the LLM has answered): every other
      Merchant with a guess is filed `auto-low:`, except the REVIEW_CAP
      Merchants with the most expected impact (spend x (1 - confidence)),
      which stay in the Review Queue to ask.

    Returns how many rows it categorised. `ids`: this user's current Category
    ids. Run after the rule chain (`categorise.run`) and after new vectors."""
    db = Scoped(conn, user_id)
    p = schema.category_prediction
    _record_decisions(db)
    suspended = {
        m
        for m in db.execute(db.select(p, p.c.model).distinct()).scalars()
        if (t := threshold(m)) is not None and _suspended(db, m, t)
    }
    done = 0
    for r in db.execute(
        db.select(p, p.c.merchant_id, p.c.category_id, p.c.model).where(
            p.c.batch.is_not(None), p.c.undone_at.is_(None)
        )
    ):
        low = r.model.startswith(LOW_MODEL)
        version = r.model.removeprefix(LOW_MODEL)
        if version not in suspended:  # a stopped version's rows go back to Review
            source = (LOW if low else SOURCE) + version
            done += _set(db, r.merchant_id, r.category_id, source)
    found: dict[str, list[tuple[int, Category]]] = {}
    model: list[str | None] = [None]

    def scored(
        targets: dict[str, int], labelled: dict[int, set[Category]]
    ) -> dict[str, list[Category]]:
        # the kNN sees the base Merchants too, not only Corrections: every
        # Category has examples, so one big corrected Category (a bulk accept
        # of Groceries) no longer claims every near miss
        model[0], hits = embedding.ranked(
            conn, user_id, targets, {**llm.labelled(conn, user_id), **labelled}
        )
        found.update(hits)
        return {}

    items = [
        i
        for i in ReviewQueue(conn, user_id, suggester=scored).items()
        if i.type == CategoryItems.name and i.merchant_id is not None
    ]
    emb = model[0]
    lv, answers = _answers(db, ids)
    if emb is None and lv is None:
        return done
    gate = f"{emb}+{lv.removeprefix(llm.PREFIX)}" if emb and lv else None

    def live(version: str | None) -> int | None:
        if version is None or version in suspended:
            return None
        return threshold(version)

    undone = set(
        db.execute(
            db.select(p, p.c.merchant_id).where(p.c.undone_at.is_not(None))
        ).scalars()
    )
    logged = {
        (r.model, r.merchant_id): r.id
        for r in db.execute(db.select(p, p.c.id, p.c.model, p.c.merchant_id))
    }
    # One sync is one batch: a pass joins the latest batch when nothing else
    # changed the ledger since that batch's own bump (its id is the version it
    # left), else it starts one. Either way it bumps when it applies anything.
    now = db.ledger_version()
    latest = db.execute(
        db.select(p, func.max(p.c.batch)).where(p.c.undone_at.is_(None))
    ).scalar()

    def place(version: str, m: int, cat: Category, score: int, source: str) -> int:
        values = {"category_id": ids[cat], "score": score, "batch": -1}
        if (version, m) in logged:
            db.execute(
                db.update(p).where(p.c.id == logged[version, m]).values(**values)
            )
        else:
            db.insert(
                p, model=version, merchant_id=m, created_at=datetime.now(UTC), **values
            )
        return _set(db, m, ids[cat], source)

    new = 0
    ask: list[tuple[Decimal, str, int, Category | None, str]] = []
    for i in sorted(items, key=lambda i: i.key):
        m = i.merchant_id
        if m is None or m in undone:
            continue
        hit = found.get(i.key)
        knn = hit[0] if emb and hit and hit[0][1] in ids else None
        said = answers.get(m)
        said = said if said in ids else None
        if knn and emb:
            score, cat = knn
            t = live(emb)
            if t is not None and score >= t:
                new += place(emb, m, cat, score, SOURCE + emb)
                continue
            t = live(gate)
            if gate and said == cat and t is not None and score >= t:
                new += place(gate, m, cat, score, SOURCE + gate)
                continue
            if (emb, m) not in logged:  # below the threshold: log the guess only
                db.insert(
                    p, model=emb, merchant_id=m, category_id=ids[cat], score=score,
                    created_at=datetime.now(UTC),
                )  # fmt: skip
                logged[emb, m] = -1
        t = live(lv)
        if said and lv and t is not None and llm.ANSWERED >= t:
            new += place(lv, m, said, llm.ANSWERED, SOURCE + lv)
            continue
        # the low tier's best guess, and how often the eval says it is right
        if gate and knn and said == knn[1]:
            kind, guess, version = "agree", said, gate
        elif said and lv and (LOW_PREFERS == "llm" or not knn):
            kind, guess, version = "llm", said, lv
        elif knn and emb:
            kind, guess, version = "knn", knn[1], emb
        else:
            kind, guess, version = "none", None, ""
        sure = LOW_CONFIDENCE.get(kind, 0)
        ask.append((i.spend * (100 - sure), i.key, m, guess, version))
    if final:
        ask.sort(key=lambda a: (-a[0], a[1]))  # the most expected impact first
        for _, _, m, guess, version in ask[REVIEW_CAP:]:
            if guess is not None:
                new += place(LOW_MODEL + version, m, guess, 0, LOW + version)
    pending = (p.c.batch == -1,)  # placed this pass, before it has an id
    if db.execute(db.select(p, p.c.id).where(*pending).limit(1)).first():
        db.bump_ledger_version()  # categories changed: caches keyed on it go stale
        batch = db.ledger_version()
        joined = (p.c.batch == latest, p.c.undone_at.is_(None))
        if latest == now:
            db.execute(db.update(p).where(*joined).values(batch=batch))
        db.execute(db.update(p).where(*pending).values(batch=batch))
    log.info("auto-apply rows=%d asked=%d", done + new, min(len(ask), REVIEW_CAP))
    return done + new


def latest_count(conn: Connection, user_id: int) -> int:
    """How many Merchants the latest live auto-applied batch holds (what `undo`
    would take back); 0 when there is none."""
    db = Scoped(conn, user_id)
    p = schema.category_prediction
    live = (p.c.batch.is_not(None), p.c.undone_at.is_(None))
    batch = db.execute(db.select(p, func.max(p.c.batch)).where(*live)).scalar()
    if batch is None:
        return 0
    return int(
        db.execute(db.select(p, func.count()).where(*live, p.c.batch == batch)).scalar()
        or 0
    )


def undo(conn: Connection, user_id: int) -> int:
    """Take back the latest auto-applied batch: its Merchants go back to the
    Review Queue and are never auto-applied again. Returns how many Merchants."""
    db = Scoped(conn, user_id)
    p, c = schema.category_prediction, schema.categorisation
    live = (p.c.batch.is_not(None), p.c.undone_at.is_(None))
    batch = db.execute(db.select(p, func.max(p.c.batch)).where(*live)).scalar()
    if batch is None:
        return 0
    merchants = list(
        db.execute(
            db.select(p, p.c.merchant_id).where(*live, p.c.batch == batch)
        ).scalars()
    )
    db.execute(
        db.update(p)
        .where(*live, p.c.batch == batch)
        .values(undone_at=datetime.now(UTC))
    )
    db.execute(
        db.update(c)
        .where(
            c.c.merchant_id.in_(merchants),
            or_(c.c.source.startswith(SOURCE), c.c.source.startswith(LOW)),
        )
        .values(source="default", category_id=_ids(conn, user_id)[UNCATEGORISED])
    )
    db.bump_ledger_version()
    log.info("auto-apply undone merchants=%d", len(merchants))
    return len(merchants)


def refresh_quietly(engine: Engine, user_id: int) -> None:
    """After import or sync: embed new Merchants, then apply or log their
    guesses. Never raises; the rules carry on alone."""
    embedding.refresh_quietly(engine, user_id)
    try:
        with engine.begin() as conn:
            apply(conn, user_id, _ids(conn, user_id))
    except Exception as e:  # noqa: BLE001
        log.warning("auto-apply failed: %s", type(e).__name__)
    llm.start(engine, user_id)  # the LLM, then the final pass, in the background
