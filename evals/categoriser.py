"""Categoriser evaluation (#112, #113, docs/ML_EVALUATIONS.md): a seeded
synthetic dataset of Merchant descriptors, the Corrections + rules baseline
(ADR 0005), the embedding nearest-neighbour model behind it, and the shared
results gate (`gate.py`).

    uv run python evals/categoriser.py           # CI: results reproduce
    uv run python evals/categoriser.py --write   # regenerate (gate.py refuses
                                                 # to lower a metric)
    uv run python evals/categoriser.py --ollama [--write]
        # the same with nomic-embed-text on the local Ollama: never in CI, and
        # into categoriser_ollama.json; synthetic descriptors only

In CI the nearest-neighbour model embeds with `TrigramEmbedder` (string
similarity), so CI never needs Ollama.

Rows in the "correction" split are the user's Merchant Corrections; the
metrics are on the "test" split only. Gold labels are written in taxonomy v1
names and go through `resolve`, so they keep working when the taxonomy is
upgraded (#116 renames v1 Categories; none of the labels used here merge).
"""

import csv
import hashlib
import io
import json
import random
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gate
from finance_agent import categorise
from finance_agent.categorise import embedding
from finance_agent.ledger import taxonomy
from metrics import expected_calibration_error, macro_f1, per_category

SEED = 112
DATASET_VERSION = 2  # v2 (#113): 136 more corrected Merchants, >= 5 per Category
LABEL_TAXONOMY_VERSION = 1  # the taxonomy the gold labels are named in
HERE = Path(__file__).parent
DATASET = HERE / "datasets" / f"categoriser_v{DATASET_VERSION}.csv"
RESULTS = HERE / "results" / "categoriser_baseline.json"
RESULTS_OLLAMA = HERE / "results" / "categoriser_ollama.json"
MODEL = "corrections+rules"
NN_MODEL = "corrections+rules+nearest-neighbour"

type Category = tuple[str, str]
type Prediction = tuple[Category | None, float]


def resolve(category: Category) -> Category:
    """A gold label in the current taxonomy's names."""
    return taxonomy.current(LABEL_TAXONOMY_VERSION, category)


@dataclass(frozen=True)
class Row:
    id: int
    split: str  # "correction" or "test"
    case: str  # clean, noise, lookalike, refund or unseen
    descriptor: str
    amount: int  # minor units; negative is money out
    category: Category  # gold, in LABEL_TAXONOMY_VERSION names


# --- the dataset ------------------------------------------------------------

F, T, S, B = "Food & Drink", "Transport", "Shopping", "Bills & Utilities"
SUB, H, HE, E = "Subscriptions", "Housing", "Health", "Entertainment"
TR, P = "Travel", "Personal care"

# Merchants a seeded alias in categorise.MERCHANTS knows
SEEDED: tuple[tuple[str, Category], ...] = (
    ("TESCO STORES", (F, "Groceries")),
    ("PRET A MANGER", (F, "Coffee")),
    ("DELIVEROO.CO.UK", (F, "Takeaway")),
    ("AMAZON.CO.UK", (S, "Online")),
    ("PAYPAL *SPOTIFY", (SUB, "Streaming")),
    ("TFL.GOV.UK/CP", (T, "Public transport")),
)
# Merchants with no alias that a descriptor rule catches
RULED: tuple[tuple[str, Category], ...] = (
    ("NCP PARKING", (T, "Parking")),
    ("ALDERS PHARMACY", (HE, "Pharmacy")),
    ("UBER TRIP", (T, "Taxi")),
    ("RIVERSIDE PETROL", (T, "Fuel")),
    ("MAPLE LETTINGS RENT", (H, "Rent")),
    ("HALDEN COUNCIL TAX", (B, "Council tax")),
    ("IRONWORKS GYM", (HE, "Fitness")),
)
# Fictional Merchants the user has corrected (two Corrections rows each)
CORRECTED: tuple[tuple[str, Category], ...] = (
    ("MARLOW GROCERS", (F, "Groceries")),
    ("THE COPPER KETTLE", (F, "Eating out")),
    ("BEAN THERE COFFEE", (F, "Coffee")),
    ("WOKWAY KITCHEN", (F, "Takeaway")),
    ("NORTHLINE RAIL", (T, "Public transport")),
    ("HARGREAVE CLOTHING", (S, "Clothing")),
    ("VOLTSPARK ELECTRONICS", (S, "Electronics")),
    ("HOMEWELL HARDWARE", (S, "Household")),
    ("BRIGHTWATER ENERGY", (B, "Energy")),
    ("CLEARSPRING WATER", (B, "Water")),
    ("KESTREL MOBILE", (B, "Phone & internet")),
    ("STREAMLY", (SUB, "Streaming")),
    ("PIXELFORGE SOFTWARE", (SUB, "Software")),
    ("ORCHARD BOOK CLUB", (SUB, "Memberships")),
    ("FIXRIGHT PLUMBING", (H, "Maintenance")),
    ("GREENLEAF DENTAL", (HE, "Medical")),
    ("ROYAL STAGE TICKETS", (E, "Events")),
    ("LEVEL UP GAMES", (E, "Games")),
    ("CRAFTERS CORNER", (E, "Hobbies")),
    ("SKYWING AIR", (TR, "Flights")),
    ("HARBOUR VIEW HOTEL", (TR, "Hotels")),
    ("SUNTRAIL HOLIDAYS", (TR, "Holidays")),
    ("SNIP AND STYLE", (P, "Hair & beauty")),
    ("PUREGLOW TOILETRIES", (P, "Toiletries")),
)
# Fictional Merchants with no Correction; a few hit a rule
UNSEEN: tuple[tuple[str, Category], ...] = (
    ("FENWICK FARM SHOP", (F, "Groceries")),
    ("BLUE DOOR BISTRO", (F, "Eating out")),
    ("MOONBEAM ROASTERS", (F, "Coffee")),
    ("ZIPLINE BUS CO", (T, "Public transport")),
    ("ATLAS OUTFITTERS", (S, "Clothing")),
    ("HEARTHSTONE ENERGY", (B, "Energy")),
    ("NIMBUS BROADBAND", (B, "Phone & internet")),
    ("CLOUDNOTE APP", (SUB, "Software")),
    ("HILLTOP CINEMA", (E, "Events")),
    ("CANVAS ART SUPPLIES", (E, "Hobbies")),
    ("PINEWOOD LODGE", (TR, "Hotels")),
    ("WILLOW SPA", (P, "Hair & beauty")),
    ("QUAYSIDE PARKING", (T, "Parking")),
    ("MEADOW PHARMACY", (HE, "Pharmacy")),
    ("SWIFT CABS", (T, "Taxi")),
    ("OAKFIELD FITNESS", (HE, "Fitness")),
)
# Descriptors that look like a Merchant or a rule keyword but are not
LOOKALIKES: tuple[tuple[str, Category], ...] = (
    ("TESCOMBE GARDEN CENTRE", (S, "Household")),
    ("PRETTY NAILS STUDIO", (P, "Hair & beauty")),
    ("AMAZONIA GRILL", (F, "Eating out")),
    ("UBER EATS", (F, "Takeaway")),
    ("FUEL JUICE BAR", (F, "Eating out")),
    ("GYMSHARK", (S, "Clothing")),
    ("RENTOKIL PEST CONTROL", (H, "Maintenance")),
    ("THE PARKING LOT DINER", (F, "Eating out")),
    ("PHARMACY BAR AND GRILL", (F, "Eating out")),
    ("SPOTLIGHT THEATRE", (E, "Events")),
    ("MARLOWE ARMS", (F, "Eating out")),
    ("KESTREL KITCHEN", (F, "Takeaway")),
    ("STREAMLINE BIKES", (E, "Hobbies")),
    ("SKYWING AIR CARGO", (S, "Online")),
)
# Money back from a Merchant; the last two carry no REFUND keyword
REFUNDS: tuple[tuple[str, Category], ...] = (
    ("AMAZON.CO.UK REFUND", (S, "Online")),
    ("TESCO STORES 2231 REFUND", (F, "Groceries")),
    ("DELIVEROO.CO.UK REFUND", (F, "Takeaway")),
    ("HARGREAVE CLOTHING REFUND", (S, "Clothing")),
    ("REFUND SKYWING AIR", (TR, "Flights")),
    ("HARBOUR VIEW HOTEL REFUND", (TR, "Hotels")),
    ("VOLTSPARK ELECTRONICS REFUND 0042", (S, "Electronics")),
    ("LEVEL UP GAMES REFUND", (E, "Games")),
    ("ATLAS OUTFITTERS REFUND", (S, "Clothing")),
    ("HILLTOP CINEMA REFUND", (E, "Events")),
    ("HARGREAVE CLOTHING", (S, "Clothing")),
    ("SUNTRAIL HOLIDAYS", (TR, "Holidays")),
)
# More fictional corrected Merchants (#113), so every Category has >= 5 and
# the Corrections clear the embedding model's data gate (ADR 0015). They are
# Corrections only: no test row names them. One clean and one noisy row each.
MORE_CORRECTED: dict[Category, tuple[str, ...]] = {
    (F, "Groceries"): (
        "HOLLYBANK GROCERY",
        "PENROSE PANTRY",
        "THISTLE FOODSTORE",
        "LARKFIELD MARKET",
    ),
    (F, "Eating out"): (
        "THE GILDED SPOON",
        "SAFFRON TABLE",
        "OLD MILL TAVERN",
        "RIVERSTONE BRASSERIE",
    ),
    (F, "Takeaway"): (
        "DRAGON GATE TAKEAWAY",
        "CRUSTWORKS PIZZA",
        "NOODLE NEST",
        "FRYDAY CHIPPY",
    ),
    (F, "Coffee"): (
        "DRIFTWOOD COFFEE",
        "PERCOLATE CAFE",
        "BREWHOUSE ESPRESSO",
        "MORNING GRIND",
    ),
    (T, "Public transport"): (
        "VALLEY BUSWAYS",
        "COASTLINE TRAMS",
        "HARBOURLINE FERRIES",
        "REDWING COACHES",
    ),
    (T, "Taxi"): (
        "NIGHTOWL CARS",
        "RAPIDRIDE MINICABS",
        "CITYHOP CHAUFFEURS",
        "BLUELINE PRIVATE HIRE",
        "STARLIGHT RIDES",
    ),
    (T, "Fuel"): (
        "CROSSROADS FORECOURT",
        "HIGHWAY SERVICE STATION",
        "GREENPUMP DIESEL",
        "MILESTONE GARAGE",
        "TOPUP FILLING STATION",
    ),
    (T, "Parking"): (
        "CASTLEGATE CAR PARK",
        "PARKSAFE BAYS",
        "KERBSIDE PAY AND DISPLAY",
        "MULTISTOREY SQUARE",
        "STATIONSIDE PARKING",
    ),
    (S, "Online"): (
        "CLICKCART MARKETPLACE",
        "PARCELPOINT SHOP",
        "BASKETBAY ONLINE",
        "NETMART DIRECT",
        "SHOPSTREAM WEB",
    ),
    (S, "Clothing"): (
        "TAILOR AND THREAD",
        "DENIM DEPOT",
        "WOOLLEN WARDROBE",
        "STRIDE FOOTWEAR",
    ),
    (S, "Electronics"): (
        "CIRCUITRY HOUSE",
        "GADGETRY STORE",
        "PIXEL AND PLUG",
        "AMPERE TECH",
    ),
    (S, "Household"): (
        "HOMESTEAD HOMEWARES",
        "KITCHENKIND",
        "LINEN AND LAMP",
        "DUSTPAN SUPPLIES",
    ),
    (B, "Energy"): (
        "SUNRISE POWER",
        "NORTHWIND ELECTRIC",
        "EMBER GAS SUPPLY",
        "BRIGHTGRID ENERGY",
    ),
    (B, "Water"): (
        "RIVERVALE WATER",
        "DEEPWELL WATER SERVICES",
        "AQUAFLOW UTILITIES",
        "MOORLAND WATER",
    ),
    (B, "Phone & internet"): (
        "SIGNALPEAK MOBILE",
        "FIBRENEST BROADBAND",
        "ECHOLINE TELECOM",
        "ORBITAL WIFI",
    ),
    (B, "Council tax"): (
        "BRAMBLEDOWN COUNCIL TAX",
        "ELMSWORTH BOROUGH COUNCIL",
        "KINGSMERE BOROUGH COUNCIL",
        "WESTHOLLOW DISTRICT COUNCIL",
        "STONEBRIDGE COUNTY COUNCIL",
    ),
    (SUB, "Streaming"): ("FLIXWAVE", "TUNEBOX MUSIC", "REELSTREAM TV", "SOUNDSPHERE"),
    (SUB, "Software"): (
        "CODECRAFT TOOLS",
        "DOCUFLOW PRO",
        "TASKNEST APP",
        "BYTEVAULT SOFTWARE",
    ),
    (SUB, "Memberships"): (
        "HERITAGE TRUST MEMBERSHIP",
        "WILDLIFE FRIENDS CLUB",
        "CHESS SOCIETY DUES",
        "HILLWALKERS CIRCLE",
    ),
    (H, "Rent"): (
        "OAKTREE PROPERTY RENT",
        "HAVEN HOMES LETTINGS",
        "CORNERSTONE ESTATES",
        "KEYSTONE RESIDENTIAL",
        "REDBRICK LANDLORDS",
    ),
    (H, "Maintenance"): (
        "HANDYHELP REPAIRS",
        "BOILERCARE SERVICES",
        "ROOFRIGHT ROOFING",
        "SPARKWISE ELECTRICIANS",
    ),
    (HE, "Pharmacy"): (
        "GREENCROSS CHEMIST",
        "WELLSPRING DISPENSARY",
        "HEALWELL CHEMISTS",
        "MEDIQUICK DRUGSTORE",
        "CORNER APOTHECARY",
    ),
    (HE, "Fitness"): (
        "PEAKFORM STUDIO",
        "IRONCLAD BOXING",
        "FLOWSTATE YOGA",
        "RIPTIDE SWIM CLUB",
        "CORESTRENGTH PILATES",
    ),
    (HE, "Medical"): (
        "BRIGHTSMILE ORTHODONTICS",
        "CLEARVIEW OPTICIANS",
        "KINDCARE CLINIC",
        "STEADYSTEP PHYSIO",
    ),
    (E, "Events"): (
        "GRAND ARENA TICKETS",
        "LIVEWIRE GIGS",
        "OPEN AIR FESTIVALS",
        "LAUGHTRACK COMEDY CLUB",
    ),
    (E, "Games"): (
        "DICE AND DRAGONS",
        "CONSOLE CORNER",
        "PIXELPLAY ARCADE",
        "QUESTLINE GAMES",
    ),
    (E, "Hobbies"): (
        "YARN AND NEEDLE",
        "MODELWORKS HOBBIES",
        "BRUSHSTROKE STUDIO",
        "GREENTHUMB GARDENING",
    ),
    (TR, "Flights"): (
        "BLUEJET AIRWAYS",
        "CLOUDHOPPER AIR",
        "HORIZON WINGS",
        "ALBATROSS AIRLINES",
    ),
    (TR, "Hotels"): (
        "CASTLE KEEP INN",
        "SEAFRONT SUITES",
        "MERIDIAN STAY",
        "LANTERN HOUSE HOTEL",
    ),
    (TR, "Holidays"): (
        "WANDERLUST TOURS",
        "ISLAND ESCAPES",
        "SUNSEEKER BREAKS",
        "ALPINE ADVENTURES",
    ),
    (P, "Hair & beauty"): (
        "GLOSS AND GLAMOUR",
        "FADE MASTERS BARBERS",
        "SERENITY NAIL BAR",
        "LUSTRE SALON",
    ),
    (P, "Toiletries"): (
        "FRESHFACE SKINCARE",
        "SUDSWORTH SOAPS",
        "MINTLEAF ORAL CARE",
        "CLEANSLATE GROOMING",
    ),
}
NOISY_VARIANTS = 4
NOISE: tuple[Callable[[str, random.Random], str], ...] = (
    lambda d, r: f"{d} {r.randint(1, 9999):04d}",  # store number
    lambda d, r: f"{d} {r.choice(('LONDON GB', 'LEEDS', 'BRISTOL', 'GBR'))}",
    lambda d, r: f"{d} CD {r.randint(1000, 9999)}",  # card suffix
    lambda d, r: f"{r.choice(('SQ *', 'SUMUP *', 'ZTL*'))}{d}",  # processor
    lambda d, _: d.lower(),
    lambda d, _: d[:15],  # truncated by the Bank
    lambda d, r: f"{d} {r.randint(1, 28):02d}{r.choice(('JAN', 'JUN', 'NOV'))}",
    lambda d, _: d.replace(" ", "  "),
)
COLUMNS = ("id", "split", "case", "descriptor", "amount_minor", "top", "sub")


def generate(seed: int) -> str:
    """The dataset CSV, deterministic for a seed."""
    rng = random.Random(seed)  # noqa: S311 - reproducible synthetic data, not security
    rows: list[tuple[str, str, str, int, Category]] = []

    def spend() -> int:
        return -rng.randint(150, 25_000)

    def noisy(d: str) -> str:
        return rng.choice(NOISE)(d, rng)

    for d, cat in CORRECTED:
        rows.append(("correction", "clean", d, spend(), cat))
        rows.append(("correction", "noise", noisy(d), spend(), cat))
    for group, case in ((SEEDED, "clean"), (RULED, "clean"), (CORRECTED, "clean")):
        for d, cat in group:
            rows.append(("test", case, d, spend(), cat))
            rows += [
                ("test", "noise", noisy(d), spend(), cat) for _ in range(NOISY_VARIANTS)
            ]
    for d, cat in UNSEEN:
        rows.append(("test", "unseen", d, spend(), cat))
        rows += [("test", "unseen", noisy(d), spend(), cat) for _ in range(2)]
    rows += [("test", "lookalike", d, spend(), cat) for d, cat in LOOKALIKES]
    rows += [("test", "refund", d, rng.randint(150, 25_000), cat) for d, cat in REFUNDS]
    # appended last, so the v1 rows keep their ids, descriptors and amounts
    for cat, names in MORE_CORRECTED.items():
        for d in names:
            rows.append(("correction", "clean", d, spend(), cat))
            rows.append(("correction", "noise", noisy(d), spend(), cat))

    out = io.StringIO()
    out.write(
        f"# synthetic\n# categoriser dataset v{DATASET_VERSION}, seed {seed},"
        f" labels in taxonomy v{LABEL_TAXONOMY_VERSION}\n"
    )
    w = csv.writer(out, lineterminator="\n")
    w.writerow(COLUMNS)
    for i, (split, case, d, amount, (top, sub)) in enumerate(rows, 1):
        w.writerow((i, split, case, d, amount, top, sub))
    return out.getvalue()


def load(path: Path = DATASET) -> list[Row]:
    lines = path.read_text().splitlines()
    while lines and lines[0].startswith("#"):  # only the header comments
        lines.pop(0)
    return [
        Row(
            int(r["id"]),
            r["split"],
            r["case"],
            r["descriptor"],
            int(r["amount_minor"]),
            (r["top"], r["sub"]),
        )
        for r in csv.DictReader(lines)
    ]


# --- the baseline and the metrics -------------------------------------------


def baseline(rows: Sequence[Row]) -> list[Prediction]:
    """Corrections + rules (ADR 0005) on the test rows, in order.

    The correction rows become Merchant Corrections, keyed by the Merchant the
    read path derives from their descriptor, and override the rule chain as
    the Ledger's Corrections overlay does. Corrections and rules are
    deterministic and claim certainty: confidence 1.0, or 0.0 for
    Uncategorised (an abstention, sent to the Review Queue).
    """

    def classify(r: Row) -> categorise.Classification:
        return categorise.classify(r.descriptor, r.amount, "DEB", transfer=False)

    corrections = {
        m: r.category
        for r in rows
        if r.split == "correction" and (m := classify(r).merchant) is not None
    }
    out: list[Prediction] = []
    for r in rows:
        if r.split != "test":
            continue
        c = classify(r)
        corrected = corrections.get(c.merchant) if c.merchant else None
        category = corrected or c.category
        if category is None or category == categorise.UNCATEGORISED:
            out.append((None, 0.0))
        else:
            out.append((resolve(category) if corrected else category, 1.0))
    return out


def nearest_neighbour(
    rows: Sequence[Row], embedder: embedding.Embedder
) -> list[Prediction]:
    """Corrections + rules, then (ADR 0005) the nearest Correction-labelled
    Merchant for what they leave Uncategorised, as the app will: one vector per
    Merchant name, cosine similarity as the confidence. No threshold here;
    #115 calibrates one."""
    base = baseline(rows)

    def merchant(r: Row) -> str | None:
        c = categorise.classify(r.descriptor, r.amount, "DEB", transfer=False)
        return c.merchant

    labelled = sorted(
        {
            m: resolve(r.category)
            for r in rows
            if r.split == "correction" and (m := merchant(r))
        }.items()
    )
    test = [r for r in rows if r.split == "test"]
    need = {
        m
        for r, (p, _) in zip(test, base, strict=True)
        if p is None and (m := merchant(r))
    }
    names = sorted({m for m, _ in labelled} | need)
    vec = dict(zip(names, embedder.embed(names), strict=True))
    out: list[Prediction] = []
    for r, (p, conf) in zip(test, base, strict=True):
        m = merchant(r)
        if p is not None or not m or not labelled:
            out.append((p, conf))
            continue
        sim, category = max(
            ((embedding.similarity(vec[m], vec[n]), c) for n, c in labelled),
            key=lambda sc: sc[0],  # ties: the first by Merchant name
        )
        out.append((category, max(sim, 0) / embedding.SIMILARITY))
    return out


def _label(c: Category) -> str:
    return f"{c[0]} / {c[1]}"


def metrics_for(
    gold: Sequence[Category], preds: Sequence[Prediction]
) -> dict[str, float]:
    """The gated metrics. `ece` counts an abstention as confidence 0 and
    wrong, so perfectly calibrated (the #119 verifier's note); `ece_assigned`
    is ECE over assigned rows only (0 when none), read beside `coverage`.
    Per-Category precision and recall are gated too, so one Category cannot
    collapse behind a steady macro F1."""
    pred = [p for p, _ in preds]
    conf = [c for _, c in preds]
    correct = [g == p for g, p in zip(gold, pred, strict=True)]
    assigned = [(c, ok) for p, c, ok in zip(pred, conf, correct, strict=True) if p]
    ece_assigned = (
        expected_calibration_error([c for c, _ in assigned], [ok for _, ok in assigned])
        if assigned
        else 0.0
    )
    out = {
        "macro_f1": round(macro_f1(gold, pred), 6),
        "ece": round(expected_calibration_error(conf, correct), 6),
        "ece_assigned": round(ece_assigned, 6),
        "accuracy": round(sum(correct) / len(gold), 6),
        "coverage": round(len(assigned) / len(gold), 6),
    }
    for c, (p, r) in sorted(per_category(gold, pred).items()):
        out[f"{_label(c)}.precision"] = round(p, 6)
        out[f"{_label(c)}.recall"] = round(r, 6)
    return out


def rules_version() -> str:
    """A fingerprint of the rule data (seeded Merchants, descriptor rules,
    location tokens, fee types), so a rule change shows as a new version of
    the baseline rather than moving its score silently."""
    rules = (
        [(m, p.pattern, c) for m, (p, c) in categorise.MERCHANTS.items()],
        [(i, p.pattern, c) for i, p, c in categorise.RULES],
        sorted(categorise.LOCATIONS),
        sorted(categorise.FEE_TYPES),
        categorise.REFUND.pattern,
    )
    return "rules-" + hashlib.sha256(repr(rules).encode()).hexdigest()[:12]


def evaluate(
    rows: Sequence[Row],
    embedder: embedding.Embedder,
    embedder_version: str | None = None,
    dataset: Path = DATASET,
) -> dict[str, Any]:
    test = [r for r in rows if r.split == "test"]
    gold = [resolve(r.category) for r in test]
    models: dict[str, Any] = {}
    for key, name, version, preds in (
        ("rules", MODEL, rules_version(), baseline(rows)),
        (
            "nn",
            NN_MODEL,
            embedder_version or embedder.model,
            nearest_neighbour(rows, embedder),
        ),
    ):
        by_case: dict[str, list[bool]] = {}
        for r, g, (p, _) in zip(test, gold, preds, strict=True):
            by_case.setdefault(r.case, []).append(g == p)
        models[key] = {
            "model": name,
            "model_version": version,
            "metrics": metrics_for(gold, preds),
            "by_case": {
                case: {"rows": len(oks), "accuracy": round(sum(oks) / len(oks), 6)}
                for case, oks in sorted(by_case.items())
            },
        }
    return {
        "harness": "categoriser",
        "dataset": dataset.name,
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "seed": SEED,
        "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
        "taxonomy_version": categorise.TAXONOMY_VERSION,
        "rows": {"correction": len(rows) - len(test), "test": len(test)},
        "support": {_label(c): n for c, n in sorted(Counter(gold).items())},
        "models": models,
    }


def lower_is_better(name: str) -> bool:
    return name.startswith("ece")


def read_results(path: Path = RESULTS) -> dict[str, Any]:
    return json.loads(path.read_text())


def main(argv: Sequence[str]) -> int:
    if "--write" in argv:
        DATASET.write_text(generate(SEED))
    if "--ollama" not in argv:
        e = embedding.TrigramEmbedder()
        return gate.run(
            "categoriser", lambda: evaluate(load(), e), RESULTS, lower_is_better, argv
        )
    ollama = embedding.OllamaEmbedder()
    digest = ollama.digest()
    if digest is None:
        print("categoriser eval: nomic-embed-text is not on Ollama", file=sys.stderr)
        return 1
    version = f"{ollama.model}@{digest[:12]}"
    return gate.run(
        "categoriser",
        lambda: evaluate(load(), ollama, version),
        RESULTS_OLLAMA,
        lower_is_better,
        argv,
        tolerance=1e-6,  # float arithmetic inside Ollama
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
