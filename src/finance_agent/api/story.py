"""The monthly money story (#276, decision 8 of #251), for `current_user` only.

GET returns the month's recap, recomputed by the Engine, with its saved
wording while the month's data is unchanged, else the deterministic template.
POST has the local model word it (short keep_alive), keeps the wording only if
it grounds against the recap (ADR 0007), else the template, and saves it
against the fingerprint it was grounded on. Nothing is logged.
"""

from collections.abc import Iterator
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi import Path as P
from pydantic import BaseModel
from sqlalchemy import Connection

from finance_agent.api.chat import model_for
from finance_agent.assistant import ChatModel, OllamaChatModel
from finance_agent.assistant.tools import story as tools
from finance_agent.engine import story
from finance_agent.identity import current_user

router = APIRouter(prefix="/api/story", tags=["story"])
Month = Annotated[str, P(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


def _conn(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.begin() as conn:
        yield conn


def story_model(request: Request) -> ChatModel:
    with request.app.state.engine.connect() as conn:
        chosen = model_for(conn).model
    return OllamaChatModel(chosen, keep_alive=tools.KEEP_ALIVE)


def today() -> date:
    return date.today()


Conn = Annotated[Connection, Depends(_conn)]


class StoryOut(BaseModel):
    recap: tools.GetStoryOut
    text: str
    worded_by: Literal["model", "template"]
    saved: bool


def _read(conn: Connection, month: str) -> story.Story:
    try:
        return story.story(conn, current_user(), month)
    except ValueError:
        raise HTTPException(422, "not a month (YYYY-MM)") from None


@router.get("/{month}")
def get_story(conn: Conn, month: Month) -> StoryOut:
    s = _read(conn, month)
    out = tools.story_out(s)
    if s.wording:
        return StoryOut(recap=out, text=s.wording, worded_by="model", saved=True)
    return StoryOut(
        recap=out, text=tools.template(out), worded_by="template", saved=False
    )


@router.post("/{month}")
def word_story(
    conn: Conn,
    month: Month,
    model: Annotated[ChatModel, Depends(story_model)],
    day: Annotated[date, Depends(today)],
) -> StoryOut:
    s = _read(conn, month)
    out = tools.story_out(s)
    text, by_model = tools.word(model, out, day)
    saved = story.save(conn, current_user(), s, text if by_model else None)
    return StoryOut(
        recap=out, text=text, worded_by="model" if by_model else "template", saved=saved
    )
