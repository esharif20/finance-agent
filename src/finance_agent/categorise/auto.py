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
from datetime import UTC, datetime

from sqlalchemy import Connection, Engine, func

from finance_agent.categorise import embedding
from finance_agent.ledger import CATEGORISED_KINDS
from finance_agent.ledger import UNCATEGORISED as _NONE
from finance_agent.ledger.review import CategoryItems, ReviewQueue
from finance_agent.storage import Scoped, schema

log = logging.getLogger(__name__)  # counts only

# model name -> threshold in similarity thousandths, copied from the committed
# synthetic calibration (a test keeps them equal)
THRESHOLDS: dict[str, int] = {"nomic-embed-text": 861, "hashed-trigrams-v1": 817}
MIN_DECISIONS = 50
PRECISION = 95  # percent
SOURCE = "auto:"

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


def apply(conn: Connection, user_id: int, ids: dict[Category, int]) -> int:
    """Re-apply live auto rows, log or apply the current guesses; returns
    how many rows it categorised. `ids`: this user's current Category ids.
    Run right after the rule chain (`categorise.run`) and after new vectors."""
    db = Scoped(conn, user_id)
    p = schema.category_prediction
    done = 0
    for r in db.execute(
        db.select(p, p.c.merchant_id, p.c.category_id, p.c.model).where(
            p.c.batch.is_not(None), p.c.undone_at.is_(None)
        )
    ):
        done += _set(db, r.merchant_id, r.category_id, SOURCE + r.model)
    _record_decisions(db)
    found: dict[str, list[tuple[int, Category]]] = {}
    model: list[str | None] = [None]

    def scored(
        targets: dict[str, int], labelled: dict[int, set[Category]]
    ) -> dict[str, list[Category]]:
        model[0], hits = embedding.ranked(conn, user_id, targets, labelled)
        found.update(hits)
        return {}

    items = [
        i
        for i in ReviewQueue(conn, user_id, suggester=scored).items()
        if i.type == CategoryItems.name and i.merchant_id is not None
    ]
    version = model[0]
    if version is None:
        return done
    threshold = THRESHOLDS.get(version.split("@")[0])
    live = threshold is not None and not _suspended(db, version, threshold)
    undone = set(
        db.execute(
            db.select(p, p.c.merchant_id).where(p.c.undone_at.is_not(None))
        ).scalars()
    )
    logged = {
        r.merchant_id: r.id
        for r in db.execute(
            db.select(p, p.c.id, p.c.merchant_id).where(p.c.model == version)
        )
    }
    batch = db.ledger_version()
    for i in sorted(items, key=lambda i: i.key):
        m = i.merchant_id
        if i.key not in found or m is None or m in undone:
            continue
        score, cat = found[i.key][0]
        if cat not in ids:
            continue
        if live and threshold is not None and score >= threshold:
            values = {"category_id": ids[cat], "score": score, "batch": batch}
            if m in logged:
                db.execute(db.update(p).where(p.c.id == logged[m]).values(**values))
            else:
                db.insert(
                    p, model=version, merchant_id=m, created_at=datetime.now(UTC),
                    **values,
                )  # fmt: skip
            done += _set(db, m, ids[cat], SOURCE + version)
        elif m not in logged:  # below the threshold: log the guess only
            db.insert(
                p, model=version, merchant_id=m, category_id=ids[cat], score=score,
                created_at=datetime.now(UTC),
            )  # fmt: skip
    log.info("auto-apply rows=%d live=%s", done, live)
    return done


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
        .where(c.c.merchant_id.in_(merchants), c.c.source.startswith(SOURCE))
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
