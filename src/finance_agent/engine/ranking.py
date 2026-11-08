"""Insight ranking (#150, docs/AI_DESIGN.md, ADR 0015): pure functions.

Score = impact x confidence x relevance, highest first; equal scores keep the
order given (Python's sort is stable), so a ranking never flickers on ties.

Relevance is a Beta-Bernoulli posterior per (Insight type, Category): Beta(1, 1)
before any signal, one success per explicit thumbs-up and one failure per
thumbs-down or dismissal. Nothing else (views, clicks) ever updates it (ADR
0015: rewards are acceptances, never engagement). Its mean is an exact
`Fraction`, so scores are exact too.

The feasibility filter then drops an Insight that a constraint Memory rules
out: one whose tags name a word of the Insight's subject (its Merchant or
Category), such as a "rent" constraint against a Rent spike. Preferences and
goals never hide an Insight.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Protocol

type Cell = tuple[str, str]  # (Insight type, Category label)


@dataclass(frozen=True)
class Relevance:
    """Beta(1 + liked, 1 + disliked)."""

    liked: int = 0
    disliked: int = 0

    def mean(self) -> Fraction:
        return Fraction(1 + self.liked, 2 + self.liked + self.disliked)

    def update(self, liked: bool) -> "Relevance":
        return Relevance(self.liked + liked, self.disliked + (not liked))


PRIOR = Relevance()


def posteriors(signals: Iterable[tuple[str, str, bool]]) -> dict[Cell, Relevance]:
    """(type, Category, liked) signals -> each cell's posterior."""
    out: dict[Cell, Relevance] = {}
    for kind, category, liked in signals:
        out[kind, category] = out.get((kind, category), PRIOR).update(liked)
    return out


class Rankable(Protocol):
    @property
    def type(self) -> str: ...
    @property
    def category(self) -> str: ...
    @property
    def impact(self) -> int: ...  # pence
    @property
    def confidence(self) -> int: ...  # percent


def score(i: Rankable, relevance: Mapping[Cell, Relevance]) -> Fraction:
    return i.impact * i.confidence * relevance.get((i.type, i.category), PRIOR).mean()


def rank[T: Rankable](
    items: Sequence[T], relevance: Mapping[Cell, Relevance]
) -> list[T]:
    """Highest score first; ties keep their given order."""
    return sorted(items, key=lambda i: -score(i, relevance))


def _words(text: str) -> set[str]:
    return set("".join(c if c.isalnum() else " " for c in text.casefold()).split())


def feasible[T](
    items: Iterable[T], subject: Callable[[T], str], constraint_tags: Iterable[str]
) -> list[T]:
    """`items` less those whose subject (its Merchant and Category) shares a
    word with a constraint Memory's tag; order kept."""
    tags = {t.casefold() for t in constraint_tags}
    return [i for i in items if not (tags & _words(subject(i)))]
