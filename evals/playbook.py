"""Playbook evaluation (#254, docs/ML_EVALUATIONS.md): do the cards'
Engine-evaluated conditions pick the cards each synthetic persona should see,
and does every figure on a shown card come from the Engine or a dated fact?

    uv run python evals/playbook.py            # CI: results reproduce
    uv run python evals/playbook.py --write    # refresh (see gate.py)

Metrics, per model:

- `<card>.precision` and `<card>.recall` of "shown" against the personas'
  gold "cards that apply". Precision with nothing shown counts as 1.0.
- `false_condition_shown`: cards shown while their own condition, evaluated
  on the persona's signals, is false (lower is better; must be 0).
- `figures_from_engine` (the playbook only): the share of numbers in rendered
  titles and bodies, in both tones, that equal a slot value or a fact value
  shown with the card. 1.0 when every number is accounted for.

Baselines: show every card (the precision floor) and show none (the recall
floor). Both are scored on the same personas.
"""

import re
import sys
from pathlib import Path
from typing import Any

import gate
import synthetic_personas as sp
from finance_agent.engine import playbook

RESULTS = Path(__file__).parent / "results" / "playbook.json"
NUMBER = re.compile(r"£?\d[\d,]*(?:\.\d+)?")
TONES = ("gentle", "blunt")


def _ratio(a: int, b: int) -> float:
    return round(a / b, 6) if b else 1.0


def evaluate() -> dict[str, Any]:
    book = playbook.load()
    ps = sp.personas()
    ids = [c.id for c in book.cards]
    shown: dict[str, list[set[str]]] = {"playbook": [], "show_all": [], "show_none": []}
    false_shown = dict.fromkeys(shown, 0)
    numbers = accounted = 0
    for p in ps:
        sigs = playbook.signals(p.rows, p.savings_balance, p.today)
        holds = {c.id for c in book.cards if playbook.holds(c.when, sigs)}
        mine: set[str] = set()
        for c in book.cards:
            for tone in TONES:
                r = playbook.render(c, sigs, book.facts, tone, p.today)
                if r is None:
                    continue
                mine.add(c.id)
                allowed = {v for _, v in r.figures} | {f.value for f in r.facts}
                for n in NUMBER.findall(f"{r.title} {r.body}"):
                    numbers += 1
                    accounted += n in allowed
        shown["playbook"].append(mine)
        shown["show_all"].append(set(ids))
        shown["show_none"].append(set())
        for key in shown:
            false_shown[key] += len(shown[key][-1] - holds)

    def metrics(key: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for cid in ids:
            tp = sum(
                cid in s and cid in p.gold for s, p in zip(shown[key], ps, strict=True)
            )
            n_shown = sum(cid in s for s in shown[key])
            n_gold = sum(cid in p.gold for p in ps)
            out[f"{cid}.precision"] = _ratio(tp, n_shown)
            out[f"{cid}.recall"] = _ratio(tp, n_gold)
        out["false_condition_shown"] = false_shown[key]
        if key == "playbook":
            out["figures_from_engine"] = _ratio(accounted, numbers)
        return out

    names = {
        "playbook": ("engine.playbook conditions", f"cards-{book.version}"),
        "show_all": ("show every card", "baseline"),
        "show_none": ("show no card", "baseline"),
    }
    return {
        "harness": "playbook",
        "dataset": "synthetic_personas",
        "dataset_version": sp.DATASET_VERSION,
        "dataset_sha256": sp.fingerprint(ps),
        "personas": len(ps),
        "gold": {p.name: sorted(p.gold) for p in ps},
        "shown": {
            p.name: sorted(s) for p, s in zip(ps, shown["playbook"], strict=True)
        },
        "numbers_checked": numbers,
        "models": {
            key: {"model": model, "model_version": version, "metrics": metrics(key)}
            for key, (model, version) in names.items()
        },
    }


def lower_is_better(name: str) -> bool:
    return name == "false_condition_shown"


if __name__ == "__main__":
    sys.exit(gate.run("playbook", evaluate, RESULTS, lower_is_better, sys.argv[1:]))
