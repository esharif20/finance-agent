# synthetic
"""Seeded synthetic Ledgers for the search evaluation (#253): everyday
Transactions as noise, one-off "things" whose names share words, Notes, Splits
and Memories, and "find that thing" queries with their gold targets.

Every name is invented. Query types:

- **exact**: a thing's name as the bank shows it;
- **misspelling**: the name with one typo in its first word;
- **paraphrase**: the place word plus a synonym for the business;
- **note**: a Note's key words, reordered;
- **split**: a Split part's Category and the thing's place word;
- **memory**: a Memory's key words, reordered.

A target is symbolic (`Target`) and resolved to document ids on the built
Ledger; a thing's gold is its Transaction and its Merchant.
"""

import hashlib
import json
import random
from typing import Literal, NamedTuple

SEED = 253
DATASET_VERSION = 1
LEDGERS = 3

PLACES = (
    "BRIGHTWATER OAKHOLLOW REDFERN MILLBROOK STONEGATE WILLOWMERE ASHCOMBE "
    "FERNDALE HOLLYBANK KESTREL LARKSPUR MOORFIELD"
).split()
# business as the bank shows it -> how a person might say it
BUSINESSES = {
    "KAYAK HIRE": "canoe rental",
    "DENTAL CARE": "dentist",
    "PET GROOMING": "dog groomer",
    "BIKE REPAIRS": "bicycle repair",
    "POTTERY STUDIO": "ceramics class",
    "VINTAGE RECORDS": "vinyl shop",
    "CLIMBING WALL": "bouldering gym",
    "FLOWER MARKET": "florist",
    "CHESS CLUB": "chess lessons",
    "TAILORS": "clothes alterations",
    "BAKEHOUSE": "bakery",
    "GARDEN CENTRE": "plant nursery",
}
NOISE = (
    "SYNTH GROCER",
    "SYNTH FUEL STATION",
    "SYNTH COFFEE CART",
    "SYNTH TRANSIT",
    "SYNTH PHARMACY",
    "SYNTH STREAMING",
    "SYNTH TAKEAWAY",
    "SYNTH CORNER SHOP",
)
PEOPLE = ("Sam", "Priya", "Tom", "Aisha", "Jonah", "Mei", "Ravi", "Elena")
EVENTS = (
    "gig tickets",
    "birthday cake",
    "taxi home",
    "hotel deposit",
    "team lunch",
    "wedding gift",
    "train fares",
    "football kit",
    "camping gear",
    "theatre seats",
)
GOALS = (
    "a trip to Lisbon",
    "new climbing shoes",
    "the car insurance renewal",
    "a second hand sofa",
    "christmas presents",
    "a refurbished laptop",
    "a wedding in June",
    "the boiler service",
    "a weekend in Edinburgh",
    "a piano for Mei",
)
MEMORY_FORMS = ("Saving for {g}", "Keep money aside for {g}", "Budget for {g}")
CATEGORIES = (
    ("Food & Drink", "Groceries"),
    ("Shopping", "Household"),
    ("Entertainment", "Hobbies"),
    ("Health", "Fitness"),
    ("Travel", "Holidays"),
    ("Personal care", "Toiletries"),
)
THINGS = 22
NOISE_ROWS = 160
NOTES = 12
SPLITS = 6
MEMORIES = 8

type QueryType = Literal[
    "exact", "misspelling", "paraphrase", "note", "split", "memory"
]
QUERY_TYPES: tuple[QueryType, ...] = (
    "exact",
    "misspelling",
    "paraphrase",
    "note",
    "split",
    "memory",
)


class Target(NamedTuple):
    kind: Literal["thing", "note", "split", "memory"]
    at: int  # a row (thing), note, split or memory index
    part: int = 0


class Query(NamedTuple):
    type: QueryType
    text: str
    target: Target


class Split(NamedTuple):
    row: int
    first_minor: int  # the first part; the second is the remainder
    categories: tuple[tuple[str, str], tuple[str, str]]


class LedgerSpec(NamedTuple):
    user_id: int
    rows: tuple[tuple[str, int], ...]  # (description, amount pence), day by day
    things: tuple[int, ...]  # row index of each thing
    notes: tuple[tuple[int, str], ...]  # (row, text)
    splits: tuple[Split, ...]
    memories: tuple[str, ...]
    queries: tuple[Query, ...]


def _typo(word: str, rnd: random.Random) -> str:
    i = rnd.randrange(1, len(word) - 1)
    if rnd.random() < 0.5 or word[i - 1] == word[i]:  # swapping "LL" is no typo
        return word[:i] + word[i + 1 :]  # a dropped letter
    return word[: i - 1] + word[i] + word[i - 1] + word[i + 1 :]  # a swap


def _ledger(user_id: int, rnd: random.Random) -> LedgerSpec:
    combos = [(p, b) for p in PLACES for b in BUSINESSES]
    picked = rnd.sample(combos, THINGS)  # place and business words repeat
    rows: list[tuple[str, int]] = [
        (f"{rnd.choice(NOISE)} {rnd.randrange(100, 999)}", -rnd.randrange(150, 6000))
        for _ in range(NOISE_ROWS)
    ]
    things: list[int] = []
    for p, b in picked:
        at = rnd.randrange(len(rows) + 1)
        rows.insert(at, (f"{p} {b}", -rnd.randrange(800, 25000)))
        things = [t + (t >= at) for t in things] + [at]
    thing_rows = set(things)
    noise_rows = [i for i in range(len(rows)) if i not in thing_rows]
    pairs = rnd.sample([(p, e) for p in PEOPLE for e in EVENTS], NOTES)
    note_rows = rnd.sample(noise_rows, NOTES)
    notes = [
        (r, f"{p} paid me back for the {e}")
        for r, (p, e) in zip(note_rows, pairs, strict=True)
    ]
    split_things = rnd.sample(range(THINGS), SPLITS)
    splits = [
        Split(
            things[t],
            rows[things[t]][1] // 2,
            tuple(rnd.sample(CATEGORIES, 2)),  # type: ignore[arg-type]
        )
        for t in split_things
    ]
    memories = [
        rnd.choice(MEMORY_FORMS).format(g=g) for g in rnd.sample(GOALS, MEMORIES)
    ]

    queries: list[Query] = []
    for i, (p, b) in enumerate(picked):
        name = f"{p} {b}"
        target = Target("thing", things[i])
        if i % 2 == 0:
            queries.append(Query("exact", name.title(), target))
        if i % 2 == 1 or i < 4:
            words = name.split()
            words[0] = _typo(words[0], rnd)
            queries.append(Query("misspelling", " ".join(words).lower(), target))
        if i < 12:
            queries.append(Query("paraphrase", f"{BUSINESSES[b]} {p.lower()}", target))
    for i, (_, text) in enumerate(notes[:11]):
        words = text.split()
        key = [words[0], *text.split(" the ", 1)[1].split()]
        rnd.shuffle(key)
        queries.append(Query("note", " ".join(key).lower(), Target("note", i)))
    for i, s in enumerate(splits):
        place = rows[s.row][0].split()[0].lower()
        part = i % 2
        queries.append(
            Query(
                "split",
                f"{s.categories[part][1].lower()} {place}",
                Target("split", i, part),
            )
        )
        if i < 5:
            other = 1 - part
            queries.append(
                Query(
                    "split",
                    f"{place} {s.categories[other][1].lower()} part",
                    Target("split", i, other),
                )
            )
    for i, m in enumerate(memories):
        key = [w for w in m.split() if len(w) > 3 and w not in ("Saving", "Keep",
               "money", "aside", "Budget")]  # fmt: skip
        rnd.shuffle(key)
        queries.append(Query("memory", " ".join(key).lower(), Target("memory", i)))
    return LedgerSpec(
        user_id,
        tuple(rows),
        tuple(things),
        tuple(notes),
        tuple(splits),
        tuple(memories),
        tuple(queries),
    )


def generate(seed: int = SEED, ledgers: int = LEDGERS) -> list[LedgerSpec]:
    rnd = random.Random(seed)  # noqa: S311 - synthetic evaluation, not security
    return [_ledger(u, rnd) for u in range(1, ledgers + 1)]


def fingerprint(specs: list[LedgerSpec]) -> str:
    return hashlib.sha256(json.dumps(specs, sort_keys=True).encode()).hexdigest()
