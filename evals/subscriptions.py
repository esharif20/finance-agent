"""Subscription recognition evaluation (#310, docs/ML_EVALUATIONS.md): a seeded
synthetic set of raw descriptor rows run through Merchant normalisation and
`subscriptions.judge`, the same path `categorise.run` takes.

    uv run python evals/subscriptions.py             # evaluate; exit 1 on regression
    uv run python evals/subscriptions.py --write     # regenerate the detector results
    uv run python evals/subscriptions.py --baseline  # re-record the frozen baseline

The baseline is the recognition before #310 (one Merchant per platform, three
charges for an unknown Merchant), recorded once and kept frozen in the results
file. Metrics: recall at 1, 2 and 3+ charges of a real Subscription, the
false-subscription rate over cases with none, and Merchant accuracy on rows
whose Merchant is known (platform split, wallet prefixes).
"""

import json
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent.categorise import MERCHANTS, normalise
from finance_agent.categorise.subscriptions import judge

SEED = 310
DATASET_VERSION = 1
PER_CASE = 40
HERE = Path(__file__).parent
RESULTS = HERE / "results" / "subscriptions.json"


@dataclass
class Case:
    name: str
    gold: bool  # the target rows are a real Subscription
    # (descriptor, posted, minor units out, expected Merchant or None)
    rows: list[tuple[str, date, int, str | None]] = field(
        default_factory=list[tuple[str, date, int, str | None]]
    )
    target: str = ""  # a descriptor of the Subscription's rows, when gold
    charges: int = 0  # paid target charges, for recall at k


def _monthly(start: date, n: int) -> list[date]:
    return [start + timedelta(days=30 * i) for i in range(n)]


def generate(seed: int = SEED) -> list[Case]:
    rnd = random.Random(seed)  # noqa: S311 - reproducible synthetic data, not security

    def start() -> date:
        return date(2025, 1, 1) + timedelta(rnd.randint(0, 200))

    makers: dict[str, Callable[[int], Case]] = {}

    def case(name: str) -> Callable[[Callable[[int], Case]], None]:
        def put(f: Callable[[int], Case]) -> None:
            makers[name] = f

        return put

    def series(c: Case, desc: str, n: int, minor: int, want: str | None) -> Case:
        s = start()
        c.rows += [(desc, d, minor, want) for d in _monthly(s, n)]
        c.target, c.charges = desc, n
        return c

    def noise(c: Case, desc: str, k: int, lo: int, hi: int, want: str | None) -> None:
        s = start()
        c.rows += [
            (desc, s + timedelta(rnd.randint(0, 150)), rnd.randint(lo, hi), want)
            for _ in range(k)
        ]

    @case("google_mixed")
    def _(n: int) -> Case:  # one platform, many products (the user report)
        c = series(Case("google_mixed", True), "GOOGLE *Google One", n, 479, None)
        c.rows = [(d, o, m, "Google One") for d, o, m, _ in c.rows]
        noise(c, "GOOGLE *Colab", 2, 999, 1199, "Google Colab")
        noise(c, f"GOOGLE *CLOUD Q{rnd.randint(10, 99)}X", 3, 50, 900, "GOOGLE CLOUD")
        noise(c, "GOOGLE *TV RENTAL", 1, 399, 401, "GOOGLE TV RENTAL")
        return c

    @case("known_direct")
    def _(n: int) -> Case:
        desc = rnd.choice(["NETFLIX.COM", "SPOTIFY P3A9", "DISNEY PLUS"])
        return series(Case("known_direct", True), desc, n, rnd.randint(599, 1299), None)

    @case("ai_service")
    def _(n: int) -> Case:
        desc, want = rnd.choice(
            [
                ("GOOGLE *Gemini", "Google AI Pro"),
                ("GOOGLE *Google AI Pro", "Google AI Pro"),
                ("CURSOR, AI POWERED IDE", "Cursor"),
                ("PERPLEXITY.AI", "Perplexity"),
                ("CLAUDE.AI SUBSCRIPTION", "Anthropic"),
                ("OPENAI *CHATGPT SUBSCR", "OpenAI"),
            ]
        )
        return series(Case("ai_service", True), desc, n, rnd.randint(1500, 2000), want)

    @case("subscription_words")
    def _(n: int) -> Case:
        desc = rnd.choice(
            ["SYNTHFIT PREMIUM", "ACMECLOUD MONTHLY", "SYNTHNEWS RENEWAL",
             "BRIGHTAPP PRO", "QUILLNOTE PLUS"]
        )  # fmt: skip
        return series(
            Case("subscription_words", True), desc, n, rnd.randint(299, 999), None
        )

    @case("wallet_tagged")
    def _(n: int) -> Case:
        desc, want = rnd.choice(
            [("CRV*SPOTIFY", "Spotify"), ("WL *NETFLIX.COM", "Netflix"),
             ("CURVE*DISNEY PLUS", "Disney Plus")]
        )  # fmt: skip
        return series(
            Case("wallet_tagged", True), desc, n, rnd.randint(599, 1299), want
        )

    @case("trial_then_paid")
    def _(n: int) -> Case:
        c = series(Case("trial_then_paid", True), "SYNTHSTREAM LTD", n, 899, None)
        first = c.rows[0][1]
        c.rows.append(
            ("SYNTHSTREAM LTD", first - timedelta(7), rnd.choice([0, 100]), None)
        )
        return c

    @case("unknown_plain")
    def _(n: int) -> Case:  # no signal: the 3-charge fallback only
        return series(Case("unknown_plain", True), "WIDGETHOST LTD", n, 1500, None)

    @case("apple_mixed")
    def _(n: int) -> Case:
        c = series(Case("apple_mixed", True), "APPLE.COM/BILL", n, 299, None)
        noise(c, "APPLE.COM/BILL", 2, 79, 499, None)
        return c

    @case("one_off_sub_like")
    def _(n: int) -> Case:
        c = Case("one_off_sub_like", False)
        s = start()
        c.rows = [
            ("SYNTH BOOKS LTD", s + timedelta(rnd.randint(0, 200)), a, None)
            for a in rnd.sample([499, 799, 999, 1499], 1 + n % 2)
        ]
        return c

    @case("wallet_cinema")
    def _(n: int) -> Case:  # Vue paid by Google Pay: not Google, not a subscription
        c = Case("wallet_cinema", False)
        noise(c, "WL *VUE*GOOG", 1 + n % 2, 1200, 2500, "Vue Cinemas")
        return c

    @case("cafe_fixed_price")
    def _(n: int) -> Case:
        c = Case("cafe_fixed_price", False)
        d = start()
        for _ in range(rnd.randint(6, 12)):
            d += timedelta(rnd.randint(3, 12))
            c.rows.append(("SQ *SYNTH CAFE", d, 350, "SYNTH CAFE"))
        return c

    @case("tier_word_trap")
    def _(n: int) -> Case:  # a tier word that names the shop, not a plan
        c = Case("tier_word_trap", False)
        desc = rnd.choice(["PRO CYCLES LTD", "PREMIUM FUELS", "PLUS SIZE OUTLET"])
        noise(c, desc, 1 + n % 2, 1500, 6000, None)
        return c

    @case("two_visits_month_apart")
    def _(n: int) -> Case:  # same price twice, a month apart, no signal
        c = Case("two_visits_month_apart", False)
        s, a = start(), rnd.randint(1500, 4000)
        c.rows = [("SYNTH GARDEN CENTRE", d, a, None) for d in _monthly(s, 2)]
        return c

    return [f(1 + i % 4) for f in makers.values() for i in range(PER_CASE)]


def _judged(rows: list[tuple[str, date, int, str | None]]) -> dict[str, bool]:
    """Each Merchant the rows normalise to -> whether it is recognised."""
    by: dict[str, list[tuple[str, date, int]]] = {}
    for desc, on, minor, _ in rows:
        by.setdefault(normalise(desc), []).append((desc, on, minor))
    out: dict[str, bool] = {}
    for m, rs in by.items():
        seed = MERCHANTS.get(m)
        out[m] = (
            judge(
                m, [d for d, _, _ in rs], [(o, a) for _, o, a in rs], seed and seed[1]
            )
            is not None
        )
    return out


def _bucket(k: int) -> str:
    return "3+" if k >= 3 else str(k)


def evaluate(cases: Sequence[Case] | None = None) -> dict[str, Any]:
    cases = generate() if cases is None else cases
    hits: dict[str, list[bool]] = {"1": [], "2": [], "3+": []}
    false: list[bool] = []
    right = known = 0
    for c in cases:
        judged = _judged(c.rows)
        for desc, _, _, want in c.rows:
            if want is not None:
                known += 1
                right += normalise(desc) == want
        if c.gold:
            hits[_bucket(c.charges)].append(judged[normalise(c.target)])
        else:
            false.append(any(judged.values()))
    every = [h for hs in hits.values() for h in hs]
    return {
        "dataset_version": DATASET_VERSION,
        "seed": SEED,
        "cases": len(cases),
        "recall_at": {k: round(sum(v) / len(v), 6) for k, v in hits.items()},
        "recall": round(sum(every) / len(every), 6),
        "false_subscription_rate": round(sum(false) / len(false), 6),
        "merchant_accuracy": round(right / known, 6),
    }


def regressions(fresh: dict[str, Any], committed: dict[str, Any]) -> list[str]:
    base, before = committed["baseline"], committed["detector"]
    out = [
        f"{k} changed"
        for k in ("dataset_version", "seed", "cases")
        if fresh[k] != base[k]
    ]
    for k, v in fresh["recall_at"].items():
        if v < base["recall_at"][k]:
            out.append(f"recall at {k} below baseline")
        if v < before["recall_at"][k]:
            out.append(f"recall at {k} fell")
    if fresh["recall"] <= base["recall"]:
        out.append("recall did not rise over the baseline")
    if fresh["false_subscription_rate"] > base["false_subscription_rate"]:
        out.append("false-subscription rate above baseline")
    if fresh["false_subscription_rate"] > before["false_subscription_rate"]:
        out.append("false-subscription rate rose")
    if fresh["merchant_accuracy"] < before["merchant_accuracy"]:
        out.append("merchant accuracy fell")
    return out


def main(argv: Sequence[str]) -> int:
    fresh = evaluate()
    committed: dict[str, Any] = (
        json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    )
    if "--baseline" in argv or "--write" in argv:
        key = "baseline" if "--baseline" in argv else "detector"
        RESULTS.write_text(json.dumps({**committed, key: fresh}, indent=2) + "\n")
        return 0
    problems = regressions(fresh, committed)
    for p in problems:
        print(p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
