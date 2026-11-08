"""Monthly story wording and retrieval evaluation (#276, docs/ML_EVALUATIONS.md).

    uv run python evals/story_queries.py            # CI: results reproduce
    uv run python evals/story_queries.py --write    # refresh (see gate.py)

Dataset: the six seeded `synthetic_history` users, every month from the
sixth (a baseline needs six months) recapped by `engine.story` and indexed as
a stored story, "today" the 15th of each history's last month. Synthetic
questions with gold months:

- dated: "How was <Month> <year>?", "How was <Month> compared with now?",
  "How did last month go?", "What's happened this month?";
- undated: "When did <Merchant> put its price up?" (a planted price rise) and
  "When did my <Subcategory> spending go up?" (a planted Category spike).

Metrics:

- `retrieval.accuracy_at_1`: the resolver's month is the gold month. Baseline:
  a date-parse-only resolver (an undated question falls to today's month);
  candidate: date parse, else `ledger.search_index.story_rank`'s best month.
- `grounding.template_pass_rate`: the deterministic template grounds (must be 1).
- `grounding.<fake>.model_pass_rate`: a scripted reply grounds and is kept;
  `grounding.<fake>.final_pass_rate`: after fallback to the template (must
  be 1). Fakes: `faithful` rewords the template keeping its figures;
  `rounding` writes the first amount as "about £N", which must fail.

Not run here (no model is loaded in CI or by agents): the real local model's
pass rate and peak memory at keep_alive 1m. Batch it later with a recorded
run against `assistant.tools.story.word`.
"""

import hashlib
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import gate
import synthetic_history as sh
from finance_agent.assistant.chat_model import FakeChatModel, Reply
from finance_agent.assistant.grounding import Month, format_figure
from finance_agent.assistant.tools import story as tools
from finance_agent.engine import story
from finance_agent.engine.detectors import Row
from finance_agent.ledger.search_index import story_rank

RESULTS = Path(__file__).parent / "results" / "story_queries.json"
DATASET_VERSION = 1
_AMOUNT = re.compile(r"£([0-9,]+)\.[0-9]{2}")

type Resolver = Callable[[str, date, dict[str, dict[str, Any]]], str | None]


def _mon(m: int) -> str:
    return f"{sh.month_start(m):%Y-%m}"


def _name(m: int, year: bool = True) -> str:
    d = sh.month_start(m)
    out = format_figure(Month(year=d.year, month=d.month))
    return out if year else out.split()[0]


class History:
    def __init__(self, h: sh.History) -> None:
        rows = [
            Row(t.id, t.date, t.merchant, t.category, t.amount)
            for t in h.txns
            if t.category != sh.NONE
        ]
        self.today = sh.month_start(h.months - 1).replace(day=15)
        self.recaps = {
            _mon(m): story.recap(rows, _mon(m))
            for m in range(sh.BASELINE_MONTHS, h.months)
        }
        self.index = {m: r.to_json() for m, r in self.recaps.items()}
        last = h.months - 1
        q: list[tuple[str, str]] = [
            (f"How was {_name(sh.BASELINE_MONTHS + 3)}?", _mon(sh.BASELINE_MONTHS + 3)),
            (f"How was {_name(last - 4, False)} compared with now?", _mon(last - 4)),
            ("How did last month go?", _mon(last - 1)),
            ("What's happened this month?", _mon(last)),
        ]
        for e in h.events:
            if e.month < sh.BASELINE_MONTHS:
                continue
            if e.type == "price_rise":
                q.append((f"When did {e.key} put its price up?", _mon(e.month)))
            elif e.type == "category_spike":
                q.append(
                    (f"When did my {e.category[1]} spending go up?", _mon(e.month))
                )
        self.questions = q


def baseline(text: str, today: date, _: dict[str, dict[str, Any]]) -> str | None:
    return tools.parse_month(text, today) or f"{today:%Y-%m}"


def candidate(text: str, today: date, index: dict[str, dict[str, Any]]) -> str | None:
    return tools.resolve(text, today, lambda q: story_rank(index, q))


def faithful(template: str) -> str:
    return "Here is how the month went.\n" + template


def rounding(template: str) -> str:
    return _AMOUNT.sub(lambda m: f"about £{m[1]}", template, count=1)


FAKES: dict[str, Callable[[str], str]] = {"faithful": faithful, "rounding": rounding}


def _retrieval(hs: Sequence[History], resolve: Resolver) -> float:
    pairs = [
        (resolve(q, h.today, h.index), gold) for h in hs for q, gold in h.questions
    ]
    return round(sum(got == gold for got, gold in pairs) / len(pairs), 6)


def _wording(
    outs: Sequence[tuple[tools.GetStoryOut, date]], fake: str
) -> dict[str, float]:
    kept = final = 0
    for o, t in outs:
        model = FakeChatModel([Reply(content=FAKES[fake](tools.template(o)))])
        text, by_model = tools.word(model, o, t)
        kept += by_model
        final += tools.grounded(text, o, t)
    n = len(outs)
    return {
        "grounding.model_pass_rate": round(kept / n, 6),
        "grounding.rejected_rate": round(1 - kept / n, 6),
        "grounding.final_pass_rate": round(final / n, 6),
    }


def evaluate() -> dict[str, Any]:
    hs = [History(h) for h in sh.generate()]
    outs = [
        (tools.story_out(story.Story(r, "", None)), h.today)
        for h in hs
        for r in h.recaps.values()
    ]
    template_ok = sum(tools.grounded(tools.template(o), o, t) for o, t in outs)
    faithful_m = _wording(outs, "faithful")
    rounding_m = _wording(outs, "rounding")
    questions = [q for h in hs for q in h.questions]
    return {
        "harness": "story_queries",
        "dataset": "synthetic_history recaps + synthetic month questions",
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": hashlib.sha256(repr(questions).encode()).hexdigest(),
        "questions": len(questions),
        "stories": len(outs),
        "models": {
            "baseline": {
                "model": "date-parse-only resolver + deterministic template",
                "model_version": "1",
                "metrics": {
                    "retrieval.accuracy_at_1": _retrieval(hs, baseline),
                    "grounding.final_pass_rate": round(template_ok / len(outs), 6),
                },
            },
            "candidate": {
                "model": "date parse + story index; FakeChatModel faithful",
                "model_version": "1",
                "metrics": {
                    "retrieval.accuracy_at_1": _retrieval(hs, candidate),
                    "grounding.model_pass_rate": faithful_m[
                        "grounding.model_pass_rate"
                    ],
                    "grounding.final_pass_rate": faithful_m[
                        "grounding.final_pass_rate"
                    ],
                },
            },
            "fake_rounding": {
                "model": "FakeChatModel rounding (negative control)",
                "model_version": "1",
                "metrics": {
                    "grounding.rejected_rate": rounding_m["grounding.rejected_rate"],
                    "grounding.final_pass_rate": rounding_m[
                        "grounding.final_pass_rate"
                    ],
                },
            },
        },
        "real_model": "pending: batched local run (pass rate, peak memory)",
    }


def lower_is_better(_: str) -> bool:
    return False


def main(argv: Sequence[str]) -> int:
    return gate.run("story_queries", evaluate, RESULTS, lower_is_better, argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
