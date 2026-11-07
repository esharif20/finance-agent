"""Categoriser evaluation (#112, docs/ML_EVALUATIONS.md): a seeded synthetic
dataset of Merchant descriptors, the Corrections + rules baseline (ADR 0005)
and the regression gate CI runs.

    uv run python evals/categoriser.py          # evaluate; exit 1 on regression
    uv run python evals/categoriser.py --write  # regenerate dataset and results

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

from finance_agent import categorise
from finance_agent.ledger import taxonomy
from metrics import expected_calibration_error, macro_f1, per_category

SEED = 112
DATASET_VERSION = 1
LABEL_TAXONOMY_VERSION = 1  # the taxonomy the gold labels are named in
HERE = Path(__file__).parent
DATASET = HERE / "datasets" / f"categoriser_v{DATASET_VERSION}.csv"
RESULTS = HERE / "results" / "categoriser_baseline.json"
MODEL = "corrections+rules"
TOLERANCE = 1e-9

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


def _label(c: Category) -> str:
    return f"{c[0]} / {c[1]}"


def evaluate(rows: Sequence[Row], dataset: Path = DATASET) -> dict[str, Any]:
    test = [r for r in rows if r.split == "test"]
    gold = [resolve(r.category) for r in test]
    preds = baseline(rows)
    pred = [p for p, _ in preds]
    correct = [g == p for g, p in zip(gold, pred, strict=True)]
    support = Counter(gold)
    by_case: dict[str, list[bool]] = {}
    for r, ok in zip(test, correct, strict=True):
        by_case.setdefault(r.case, []).append(ok)
    return {
        "dataset": dataset.name,
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "seed": SEED,
        "label_taxonomy_version": LABEL_TAXONOMY_VERSION,
        "taxonomy_version": categorise.TAXONOMY_VERSION,
        "model": MODEL,
        "model_version": None,  # no learned model in the baseline
        "rows": {"correction": len(rows) - len(test), "test": len(test)},
        "macro_f1": round(macro_f1(gold, pred), 6),
        "ece": round(expected_calibration_error([c for _, c in preds], correct), 6),
        "accuracy": round(sum(correct) / len(test), 6),
        "coverage": round(sum(p is not None for p in pred) / len(test), 6),
        "by_case": {
            case: {"rows": len(oks), "accuracy": round(sum(oks) / len(oks), 6)}
            for case, oks in sorted(by_case.items())
        },
        "per_category": {
            _label(c): {
                "precision": round(p, 6),
                "recall": round(r, 6),
                "support": support[c],
            }
            for c, (p, r) in sorted(per_category(gold, pred).items())
        },
    }


def read_results(path: Path = RESULTS) -> dict[str, Any]:
    return json.loads(path.read_text())


def regressions(fresh: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    """Why `fresh` may not replace `committed`; [] when it may."""
    problems: list[str] = []
    if fresh["dataset_sha256"] != committed["dataset_sha256"]:
        problems.append("dataset changed: regenerate results with --write")
    if fresh["macro_f1"] < committed["macro_f1"] - TOLERANCE:
        problems.append(f"macro F1 {fresh['macro_f1']} < {committed['macro_f1']}")
    if fresh["ece"] > committed["ece"] + TOLERANCE:
        problems.append(f"ECE {fresh['ece']} > {committed['ece']}")
    return problems


def main(argv: Sequence[str]) -> int:
    if "--write" in argv:
        DATASET.write_text(generate(SEED))
        RESULTS.write_text(json.dumps(evaluate(load()), indent=2) + "\n")
    fresh, committed = evaluate(load()), read_results()
    for problem in regressions(fresh, committed):
        print(f"categoriser eval: {problem}", file=sys.stderr)
    print(
        f"categoriser eval: macro F1 {fresh['macro_f1']} (committed"
        f" {committed['macro_f1']}), ECE {fresh['ece']} (committed {committed['ece']})"
    )
    return 1 if regressions(fresh, committed) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
