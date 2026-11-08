"""#215 F: the agreement gate. Confident (`auto:`) when the kNN alone clears its
threshold or the kNN and the LLM agree above theirs; every other Merchant with
a signal is filed `auto-low:` on the final pass, except the REVIEW_CAP with
the most expected impact, which are asked. Undo per batch, Corrections win, an
override of a low row becomes a few-shot example. Synthetic payees, the
trigram stand-in embedder and a scripted classifier only."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.categorise import auto, embedding, llm
from finance_agent.categorise.embedding import TrigramEmbedder
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from test_categorise_embedding import _import  # pyright: ignore[reportPrivateUsage]

GROCERIES = ("Food & Drink", "Groceries")
EATING = ("Food & Drink", "Eating out")
COFFEE = ("Food & Drink", "Coffee")
UNCAT = categorise.UNCATEGORISED
EMB = TrigramEmbedder.model
LLM = "qwen-test"
GATE = f"{EMB}+{LLM}@abc123"
LABELLED = {"ZZQ MARKET NORTH": GROCERIES, "QQX NOODLE BAR": EATING}
# 14 Merchants the kNN guesses; spend rises with the number
GUESSED = [f"ZZQ MARKET {chr(65 + i)}{chr(65 + i)}" for i in range(14)]
RESULTS = Path(__file__).parents[1] / "evals" / "results"


class Scripted(llm.OllamaClassifier):
    def __init__(self, answers: Mapping[str, tuple[str, str] | None]) -> None:
        super().__init__(LLM)
        self.answers, self.asked = answers, list[str]()

    def digest(self) -> str:
        return "abc123def456"

    def classify(self, p: llm.Payee) -> tuple[str, str] | None:
        self.asked.append(p.name)
        return self.answers.get(p.name)


@pytest.fixture(autouse=True)
def _thresholds(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    monkeypatch.setitem(auto.THRESHOLDS, EMB, 1001)  # the kNN alone: never
    monkeypatch.setitem(auto.THRESHOLDS, f"{EMB}+{LLM}", 1)  # agreement: always
    monkeypatch.setattr(auto, "LOW_CONFIDENCE", {"agree": 90, "llm": 50, "knn": 20})


def _setup(engine: Engine, tmp_path: Path, n: int = len(GUESSED)) -> None:
    rows = [(name, -500) for name in LABELLED]
    rows += [(name, -100 * (i + 1)) for i, name in enumerate(GUESSED[:n])]
    with engine.begin() as c:
        _import(c, tmp_path, 1, rows)
        led = Ledger(c, 1)
        ids = {m.name: m.id for m in led.merchants()}
        for name, cat in LABELLED.items():
            led.correct_merchant(ids[name], cat)
        embedding.refresh(c, 1, TrigramEmbedder())
        categorise.run(c, 1)


def _final(engine: Engine, answers: Mapping[str, tuple[str, str] | None]) -> Scripted:
    s = Scripted(answers)
    llm.refresh(engine, 1, s)
    with engine.begin() as c:
        auto.apply(c, 1, auto.ids(c, 1), final=True)
    return s


def _sources(engine: Engine) -> dict[str, tuple[object, str]]:
    with engine.begin() as c:
        return {
            t.categorisation.merchant: (
                t.categorisation.category,
                t.categorisation.source,
            )
            for t in Ledger(c, 1).transactions()
            if t.categorisation and t.categorisation.merchant
        }


def _review(engine: Engine) -> set[str]:
    with engine.begin() as c:
        return {i.title for i in ReviewQueue(c, 1).items() if i.type == "category"}


def test_the_llm_is_asked_once_per_merchant_never_per_row(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path, 3)
    with engine.begin() as c:  # a second row for one Merchant
        _import(c, tmp_path, 1, [(GUESSED[0], -999)])
    first = _final(engine, {})
    assert sorted(first.asked) == sorted(GUESSED[:3])
    again = _final(engine, {})
    assert again.asked == []  # cached per Merchant and model version


def test_agreement_files_confidently_under_the_gate_version(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path, 3)
    _final(engine, dict.fromkeys(GUESSED[:3], GROCERIES))
    got = _sources(engine)
    assert all(got[n] == (GROCERIES, f"auto:{GATE}") for n in GUESSED[:3])


def test_review_holds_only_the_cap_with_most_impact_and_files_the_rest_low(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path)
    _final(engine, dict.fromkeys(GUESSED, COFFEE))  # every one disagrees
    got = _sources(engine)
    asked = _review(engine)
    assert len(asked) == auto.REVIEW_CAP
    # same confidence for all, so the biggest spenders are the ones asked
    assert asked == set(GUESSED[-auto.REVIEW_CAP :])
    for n in GUESSED[: -auto.REVIEW_CAP]:  # the LLM wins a disagreement (eval)
        assert got[n] == (COFFEE, f"auto-low:{llm.PREFIX}{LLM}@abc123")


def test_every_merchant_with_a_signal_gets_a_category_or_is_asked(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path)
    _final(engine, {})  # the LLM unsure on all: the kNN's guess is filed low
    got = _sources(engine)
    asked = _review(engine)
    for n in GUESSED:
        assert n in asked or got[n] == (GROCERIES, f"auto-low:{EMB}")


def test_the_import_pass_never_files_low_only_the_final_pass_does(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path)
    assert all(_sources(engine)[n][1] == "default" for n in GUESSED)
    _final(engine, {})
    with engine.begin() as c:
        categorise.run(c, 1)  # a later full recompute keeps the low rows
    assert sum(_sources(engine)[n][1].startswith("auto-low:") for n in GUESSED) == (
        len(GUESSED) - auto.REVIEW_CAP
    )


def test_one_undo_takes_back_the_low_rows_too(engine: Engine, tmp_path: Path) -> None:
    _setup(engine, tmp_path)
    _final(engine, {})
    with engine.begin() as c:
        assert auto.undo(c, 1) == len(GUESSED) - auto.REVIEW_CAP
    assert all(_sources(engine)[n] == (UNCAT, "default") for n in GUESSED)
    _final(engine, {})  # and they never come back
    assert all(_sources(engine)[n] == (UNCAT, "default") for n in GUESSED)


def test_an_override_of_a_low_row_wins_and_becomes_a_few_shot_example(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path)
    _final(engine, {})
    low = GUESSED[0]
    with engine.begin() as c:
        led = Ledger(c, 1)
        m = next(x.id for x in led.merchants() if x.name == low)
        led.correct_merchant(m, COFFEE)
        categorise.run(c, 1)
        assert llm.labelled(c, 1)[m] == {COFFEE}
        decided = (
            c.exec_driver_sql(
                "SELECT DISTINCT decided_category_id FROM category_prediction"
                " WHERE merchant_id = ?",
                (m,),
            )
            .scalars()
            .all()
        )
        assert decided == [auto.ids(c, 1)[COFFEE]]  # the live monitor saw it
    assert _sources(engine)[low] == (COFFEE, "correction")


def test_an_llm_answer_outside_the_users_categories_is_ignored(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path, 2)
    _final(engine, dict.fromkeys(GUESSED[:2], ("Transport", "Rockets")))
    assert all(_sources(engine)[n][1] != f"auto:{GATE}" for n in GUESSED[:2])


def test_the_app_gate_constants_are_the_committed_synthetic_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()
    files = sorted(RESULTS.glob("categoriser_gate_*.json"))
    assert files
    for f in files:
        r = json.loads(f.read_text())
        cal = r["calibration"]
        assert cal["confident_models"] == [] and auto.THRESHOLDS == {}  # #287
        if r["llm"].split("@")[0] == llm.PREFIX + llm.MODEL:  # the app's model
            assert auto.LOW_PREFERS == cal["low_prefers"]
            assert auto.LOW_CONFIDENCE == cal["low_confidence"]


LLM_LOW = f"low:{llm.PREFIX}{LLM}@abc123"


def test_llm_only_low_rows_are_monitored_and_stopped_when_overridden(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auto, "MIN_DECISIONS", 3)
    monkeypatch.setattr(auto, "REVIEW_CAP", 0)
    _setup(engine, tmp_path)
    _final(engine, dict.fromkeys(GUESSED, COFFEE))  # LLM-only low guesses
    assert all(_sources(engine)[n][1] == "auto-low:" + LLM_LOW[4:] for n in GUESSED)
    wrong = GUESSED[:8]  # more than half overridden: below LOW_FLOOR
    with engine.begin() as c:
        led = Ledger(c, 1)
        ids = {m.name: m.id for m in led.merchants()}
        for n in wrong:
            led.correct_merchant(ids[n], GROCERIES)
        categorise.run(c, 1)
        stopped = c.exec_driver_sql("SELECT model FROM model_suspension").scalars()
        assert list(stopped) == [LLM_LOW]
    _final(engine, dict.fromkeys(GUESSED, COFFEE))
    got = _sources(engine)
    assert all(got[n] == (UNCAT, "default") for n in GUESSED[8:])  # back to Review
    assert all(got[n][1] == "correction" for n in wrong)


def test_merchants_with_no_guess_count_inside_the_review_cap(
    engine: Engine, tmp_path: Path
) -> None:
    _setup(engine, tmp_path)
    blind = [f"WWV NOVEC {c}" for c in "XYZ"]
    with engine.begin() as c:  # imported after the vectors: no kNN signal
        _import(c, tmp_path, 1, [(n, -50_000) for n in blind])
    _final(engine, {})  # and the LLM unsure: no guess at all
    asked = _review(engine)
    assert len(asked) == auto.REVIEW_CAP
    assert set(blind) <= asked
    assert asked - set(blind) == set(GUESSED[-(auto.REVIEW_CAP - len(blind)) :])
