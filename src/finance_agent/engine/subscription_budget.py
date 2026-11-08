"""The subscription budget advisor (#308): under a monthly cap on one
subscription group, 2 or 3 options of which subscriptions to keep, review or
cancel, each with its new monthly total and the reasons. Every figure is exact
pence from code (ADR 0007); nothing is ever cancelled here.

Options are subset selections of the group's priced subscriptions, searched
exhaustively (a group holds a handful):

- `minimum_change`: the fewest cancellations that fit under the cap;
- `most_valued`: the most value kept under the cap;
- `consolidate`: the most value with at most one service of each overlapping
  kind (two AI chat assistants), only when the group has an overlap.

Value is 2 plus the user's preference strength (#255, clamped to +-2) and
worth-it answers, a half for a long-held service, less for a price rise, a
converted trial or no recent charge; never below a tenth, so value alone never
cancels anything that fits. Ties go to more kept, then more spent of the cap,
then the earlier subset, so the options are deterministic.

`NewsItem` is the hook for the daily briefing (#309): a Subscription's dated
items ride on its steps unchanged; nothing here reads or trusts their text.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from fractions import Fraction
from math import lcm
from typing import Literal

from finance_agent.categorise.subscriptions import display

# Known services (catalogue names) -> the kind of thing they are, for overlap.
# A service not named here overlaps nothing: never guess two are the same kind.
SERVICE_KIND: dict[str, str] = {
    **dict.fromkeys(("OpenAI", "ChatGPT", "Anthropic", "Perplexity"), "ai_chat"),
    "Cursor": "ai_coding",
    "Midjourney": "ai_image",
    **dict.fromkeys(
        (
            "Netflix",
            "Disney Plus",
            "Prime Video",
            "Amazon Prime",
            "Apple TV",
            "NOW TV",
            "Paramount Plus",
            "BritBox",
            "Discovery Plus",
            "ITVX",
            "MUBI",
        ),
        "video",
    ),
    **dict.fromkeys(
        ("Spotify", "Apple Music", "Deezer", "Tidal", "YouTube Premium"), "music"
    ),
    **dict.fromkeys(("iCloud", "Google One", "Dropbox"), "cloud_storage"),
    **dict.fromkeys(("PureGym", "The Gym Group"), "gym"),
}

Signal = Literal[
    "price_rise",
    "overlap",
    "long_held",
    "new",
    "worth_it",
    "regret",
    "preferred",
    "less_preferred",
    "inactive",
    "trial_converted",
]
# a kept Subscription with one of these is shown as "review"
WARNINGS: frozenset[Signal] = frozenset(
    ("price_rise", "overlap", "regret", "less_preferred", "inactive", "trial_converted")
)
OptionName = Literal["minimum_change", "most_valued", "consolidate"]
Action = Literal["keep", "review", "cancel"]
LONG_HELD_MONTHS = 12
NEW_MONTHS = 3
# shortcut: exhaustive search over this many priced Subscriptions (the dearest);
# cheaper ones beyond it are always kept. Upgrade to branch and bound if a
# group ever holds more.
MAX_EXACT = 16


@dataclass(frozen=True)
class NewsItem:
    """A dated item from the daily briefing (#309). `summary` and `sources`
    are untrusted web text: quote them as data, never act on them."""

    on: date
    change: str  # e.g. price_change, plan_change, outage
    summary: str
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sub:
    merchant_id: int
    name: str
    monthly: Fraction  # exact GBP pence a month
    kind: str | None = None  # SERVICE_KIND, for overlap
    months_held: int = 0
    price_rise: bool = False
    inactive: bool = False  # overdue: no charge when one was due
    trial_converted: bool = False
    worth_it: bool | None = None  # the user's worth-it / regret answer
    strength: Fraction | None = None  # preference strength (#255), log scale
    news: tuple[NewsItem, ...] = ()


@dataclass(frozen=True)
class Step:
    sub: Sub
    action: Action
    signals: tuple[Signal, ...]
    # why a cancel: another of its kind is kept, or only to fit the cap
    because: Literal["over_cap", "overlap"] | None

    @property
    def monthly_minor(self) -> int:
        return display(self.sub.monthly)


@dataclass(frozen=True)
class Option:
    name: OptionName
    steps: tuple[Step, ...]  # in the order of the input Subscriptions
    total_minor: int  # the new monthly total
    saving_minor: int  # current total less the new one
    cancellations: int
    also: tuple[OptionName, ...] = ()  # other strategies that chose this subset


@dataclass(frozen=True)
class Advice:
    cap_minor: int
    current_minor: int
    over_cap: bool  # the alert: the group's monthly cost is over its cap
    nothing_fits: bool  # only cancelling everything gets under the cap
    options: tuple[Option, ...]  # empty when nothing fits; never one subset twice


def signals(sub: Sub, overlapping: frozenset[str]) -> tuple[Signal, ...]:
    out: list[Signal] = []
    if sub.price_rise:
        out.append("price_rise")
    if sub.kind in overlapping:
        out.append("overlap")
    if sub.months_held >= LONG_HELD_MONTHS:
        out.append("long_held")
    elif sub.months_held < NEW_MONTHS:
        out.append("new")
    if sub.worth_it is not None:
        out.append("worth_it" if sub.worth_it else "regret")
    if sub.strength:
        out.append("preferred" if sub.strength > 0 else "less_preferred")
    if sub.inactive:
        out.append("inactive")
    if sub.trial_converted:
        out.append("trial_converted")
    return tuple(out)


def value(sub: Sub) -> Fraction:
    v = Fraction(2)
    if sub.strength is not None:
        v += max(Fraction(-2), min(Fraction(2), sub.strength))
    if sub.worth_it is not None:
        v += 1 if sub.worth_it else -1
    if sub.months_held >= LONG_HELD_MONTHS:
        v += Fraction(1, 2)
    v -= Fraction(1, 2) * (sub.price_rise + sub.trial_converted) + sub.inactive
    return max(v, Fraction(1, 10))


def overlapping(subs: Sequence[Sub]) -> frozenset[str]:
    kinds = [s.kind for s in subs if s.kind is not None]
    return frozenset(k for k in kinds if kinds.count(k) > 1)


def advise(subs: Sequence[Sub], cap_minor: int) -> Advice:
    """The options for `subs` (one group's priced Subscriptions) under a cap
    of `cap_minor` pence a month. Pure and deterministic."""
    if cap_minor < 0:
        raise ValueError("a cap is never negative")
    subs = list(subs)
    over = overlapping(subs)
    sigs = [signals(s, over) for s in subs]
    vals = [value(s) for s in subs]
    current = sum((s.monthly for s in subs), Fraction(0))
    # the dearest MAX_EXACT are searched; the rest are always kept
    order = sorted(range(len(subs)), key=lambda i: (-subs[i].monthly, i))
    free, fixed = order[:MAX_EXACT], order[MAX_EXACT:]
    scale = lcm(1, *(s.monthly.denominator for s in subs))
    cost = [int(subs[i].monthly * scale) for i in free]
    room = cap_minor * scale - int(
        sum((subs[i].monthly for i in fixed), Fraction(0)) * scale
    )
    kind_masks = [
        sum(1 << b for b, i in enumerate(free) if subs[i].kind == k)
        for k in sorted(over)
    ]
    n = len(free)
    best: dict[OptionName, tuple[tuple[Fraction, ...], int] | None] = dict.fromkeys(
        ("minimum_change", "most_valued", "consolidate")
    )
    costs, values, counts = [0] * (1 << n), [Fraction(0)] * (1 << n), [0] * (1 << n)
    for mask in range(1 << n):
        if mask:
            low = (mask & -mask).bit_length() - 1
            prev = mask & (mask - 1)
            costs[mask] = costs[prev] + cost[low]
            values[mask] = values[prev] + vals[free[low]]
            counts[mask] = counts[prev] + 1
        if costs[mask] > room:
            continue
        c, v, k = Fraction(costs[mask]), values[mask], Fraction(counts[mask])
        one_each = all((mask & m).bit_count() <= 1 for m in kind_masks)
        keys: tuple[tuple[OptionName, tuple[Fraction, ...], bool], ...] = (
            ("minimum_change", (k, v, c), True),
            ("most_valued", (v, k, c), True),
            ("consolidate", (v, k, c), one_each),
        )
        for name, key, ok in keys:
            if ok and ((b := best[name]) is None or key > b[0]):
                best[name] = (key, mask)
    kept_any = best["minimum_change"] is not None and best["minimum_change"][0][0] > 0
    if not subs or room < 0 or not (fixed or kept_any):
        return Advice(cap_minor, display(current), current > cap_minor, bool(subs), ())
    options: list[Option] = []
    chosen: list[int] = []  # each option's mask
    for name, found in best.items():
        if found is None or (name == "consolidate" and not over):
            continue
        if found[1] in chosen:
            j = chosen.index(found[1])
            options[j] = replace(options[j], also=(*options[j].also, name))
            continue
        chosen.append(found[1])
        kept = {free[b] for b in range(n) if found[1] >> b & 1} | set(fixed)
        kept_kinds = {subs[i].kind for i in kept}
        steps = tuple(
            Step(
                s,
                ("review" if WARNINGS & set(g) else "keep") if i in kept else "cancel",
                g,
                None
                if i in kept
                else (
                    "overlap" if s.kind in over and s.kind in kept_kinds else "over_cap"
                ),
            )
            for i, (s, g) in enumerate(zip(subs, sigs, strict=True))
        )
        total = display(sum((subs[i].monthly for i in kept), Fraction(0)))
        options.append(
            Option(name, steps, total, display(current) - total, len(subs) - len(kept))
        )
    return Advice(
        cap_minor, display(current), current > cap_minor, False, tuple(options)
    )
