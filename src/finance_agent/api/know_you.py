"""/api/know-you (#282): what the app has learned that the pairwise screen does
not already show: the bandit's shadow standing against the live ranker (from
off-policy evaluation, never a figure the model made up), confirmed life
changes and confirmed map edges, each resettable, for `current_user` only.
Pairwise strengths and their reset stay at /api/preferences. Nothing here logs."""

from collections.abc import Iterator
from fractions import Fraction
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Connection

from finance_agent.engine import bandit, ope
from finance_agent.identity import current_user
from finance_agent.ledger.exposure import Exposures
from finance_agent.ledger.life_changes import LifeChanges
from finance_agent.ledger.map_edges import MapEdges


class BanditOut(BaseModel):
    rated: int  # shown Insights the user gave an explicit outcome on
    live_pct: int | None  # share of those that went well, under the live ranker
    shadow_pct: int | None  # what the bandit would have scored (SNIPS), same scale


class LifeChangeOut(BaseModel):
    series: str
    month: str  # YYYY-MM


class KnowYouOut(BaseModel):
    bandit: BanditOut
    life_changes: list[LifeChangeOut]
    map_edges: int


class DeletedOut(BaseModel):
    deleted: int


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


Conn = Annotated[Connection, Depends(_conn)]
router = APIRouter(prefix="/api/know-you")


def _pct(x: Fraction | None) -> int | None:
    return None if x is None else round(x * 100)


def standing(log: Exposures) -> BanditOut:
    logs: list[tuple[ope.Logged, ope.Logged]] = []
    for e in log.logged("insight"):
        if e.outcome is None or "ts_q" not in e.context:
            continue
        r = Fraction(bandit.reward(e.outcome))
        target = Fraction(int(e.context["ts_q"]), bandit.DRAWS + 1)
        logs.append(
            (
                ope.Logged(r, e.propensity, e.propensity),
                ope.Logged(r, e.propensity, target),
            )
        )
    # live target = its own propensity, so its SNIPS is the plain mean
    return BanditOut(
        rated=len(logs),
        live_pct=_pct(ope.snips(a for a, _ in logs)),
        shadow_pct=_pct(ope.snips(b for _, b in logs)),
    )


@router.get("")
def summary(conn: Conn) -> KnowYouOut:
    user = current_user()
    return KnowYouOut(
        bandit=standing(Exposures(conn, user)),
        life_changes=[
            LifeChangeOut(series=s, month=m)
            for s, m in LifeChanges(conn, user).confirmed()
        ],
        map_edges=len(MapEdges(conn, user).stored()),
    )


@router.delete("/bandit")
def reset_bandit(conn: Conn) -> DeletedOut:
    return DeletedOut(deleted=Exposures(conn, current_user()).forget_bandit())


@router.delete("/life-changes")
def reset_life_changes(conn: Conn) -> DeletedOut:
    return DeletedOut(deleted=LifeChanges(conn, current_user()).forget())


@router.delete("/map")
def reset_map(conn: Conn) -> DeletedOut:
    return DeletedOut(deleted=MapEdges(conn, current_user()).forget())
