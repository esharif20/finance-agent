"""#215 B: confident embedding guesses auto-apply right after a sync, gated
only by the calibrated threshold; undoable per batch, never again for an
undone Merchant, and a Correction always wins. Every guess is logged as a
live accuracy record that suspends a version whose agreement falls below 95%.
Synthetic Merchants and the trigram stand-in embedder only."""

import json
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine, text

from finance_agent import categorise
from finance_agent.categorise import auto, embedding
from finance_agent.categorise.embedding import TrigramEmbedder
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from finance_agent.storage import open_db
from test_categorise_embedding import _import  # pyright: ignore[reportPrivateUsage]

GROCERIES: tuple[str, str] = ("Food & Drink", "Groceries")
EATING: tuple[str, str] = ("Food & Drink", "Eating out")
UNCAT = categorise.UNCATEGORISED
LABELLED = {
    "ZZQ MARKET NORTH": GROCERIES,
    "ZZQ MARKET SOUTH": GROCERIES,
    "QQX NOODLE BAR": EATING,
    "QQX NOODLE HOUSE": EATING,
}
DECIDE = {"ZZQ MARKET EAST": GROCERIES, "QQX NOODLE STALL": EATING}
LATER = ["ZZQ MARKET WEST", "QQX NOODLE CART"]
ODD = "WWV UNRELATED"
GUESSED = [*DECIDE, *LATER, ODD]
MODEL = TrigramEmbedder.model
OFF = 1001  # above any similarity: nothing applies
AUTO = f"auto:{MODEL}"
RESULTS = Path(__file__).parents[1] / "evals" / "results"


@pytest.fixture(autouse=True)
def _off(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Each test sets the stand-in model's threshold itself."""
    monkeypatch.setitem(auto.THRESHOLDS, MODEL, OFF)


def _rows() -> list[tuple[str, int]]:
    names = [*LABELLED, *GUESSED]
    return [(n, -500) for n in names] + [(n, -700) for n in names]


def _setup(conn: Connection, tmp_path: Path) -> None:
    _import(conn, tmp_path, 1, _rows())
    _decide(conn, LABELLED)
    assert embedding.refresh(conn, 1, TrigramEmbedder()) is not None
    categorise.run(conn, 1)  # logs a guess for every Review item


@pytest.fixture
def conn(engine: Engine, tmp_path: Path) -> Iterator[Connection]:
    with engine.begin() as c:
        _setup(c, tmp_path)
        yield c


def _ids(conn: Connection) -> dict[str, int]:
    return {m.name: m.id for m in Ledger(conn, 1).merchants()}


def _by_merchant(conn: Connection) -> dict[str, set[tuple[object, str]]]:
    out: dict[str, set[tuple[object, str]]] = {}
    for t in Ledger(conn, 1).transactions():
        c = t.categorisation
        if c is not None and c.merchant:
            out.setdefault(c.merchant, set()).add((c.category, c.source))
    return out


def _scores(conn: Connection) -> dict[str, int]:
    names = {i: n for n, i in _ids(conn).items()}
    rows = conn.execute(text("SELECT merchant_id, score FROM category_prediction"))
    return {names[m]: s for m, s in rows}


def _decide(conn: Connection, decisions: Mapping[str, tuple[str, str]]) -> None:
    led, ids = Ledger(conn, 1), _ids(conn)
    for n, cat in decisions.items():
        led.correct_merchant(ids[n], cat)


def _threshold(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setitem(auto.THRESHOLDS, MODEL, value)


def _floor(conn: Connection) -> int:
    """A threshold the stand-in clears for the ZZQ/QQX guesses but not ODD."""
    scores = _scores(conn)
    floor = min(scores[n] for n in [*DECIDE, *LATER])
    assert scores[ODD] < floor
    return floor


def _suspensions(conn: Connection) -> int:
    return conn.execute(text("SELECT count(*) FROM model_suspension")).scalar() or 0


def test_the_app_thresholds_are_the_committed_synthetic_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.undo()  # the real table, not the autouse override
    for name in ("categoriser_baseline.json", "categoriser_ollama.json"):
        nn = json.loads((RESULTS / name).read_text())["models"]["nn"]
        model = nn["model_version"].split("@")[0]
        assert auto.THRESHOLDS[model] == nn["calibration"]["threshold"]
        assert nn["calibration"]["precision"] >= 0.95


def test_every_review_item_guess_is_logged_with_ids_and_scores_only(
    conn: Connection,
) -> None:
    assert set(_scores(conn)) == set(GUESSED)
    cols = conn.execute(text("SELECT * FROM category_prediction")).keys()
    assert set(cols) == {
        "id", "user_id", "model", "merchant_id", "category_id", "score",
        "created_at", "decided_category_id", "batch", "undone_at",
    }  # fmt: skip


def test_confident_guesses_apply_at_once_with_no_answers_needed(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _threshold(monkeypatch, _floor(conn))
    categorise.run(conn, 1)
    got = _by_merchant(conn)
    for n in ["ZZQ MARKET EAST", "ZZQ MARKET WEST"]:
        assert got[n] == {(GROCERIES, AUTO)}
    for n in ["QQX NOODLE STALL", "QQX NOODLE CART"]:
        assert got[n] == {(EATING, AUTO)}
    assert got[ODD] == {(UNCAT, "default")}  # low confidence: still to review
    review = {i.title for i in ReviewQueue(conn, 1).items() if i.type == "category"}
    assert review == {ODD}


def test_low_live_agreement_stops_that_version_auto_applying(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auto, "MIN_DECISIONS", 2)
    _threshold(monkeypatch, _floor(conn))
    categorise.run(conn, 1)
    # the user overrides two auto rows: 0 of 2 agree
    _decide(conn, {"ZZQ MARKET EAST": EATING, "QQX NOODLE STALL": GROCERIES})
    categorise.run(conn, 1)
    assert _suspensions(conn) == 1
    _threshold(monkeypatch, 1)  # ODD would apply now, but the version is stopped
    categorise.run(conn, 1)
    got = _by_merchant(conn)
    assert got[ODD] == {(UNCAT, "default")}
    assert got["ZZQ MARKET WEST"] == {(GROCERIES, AUTO)}  # earlier rows stay
    assert got["ZZQ MARKET EAST"] == {(EATING, "correction")}


def test_agreement_or_too_few_answers_never_suspend(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _threshold(monkeypatch, _floor(conn))
    categorise.run(conn, 1)
    assert auto.MIN_DECISIONS == 50
    _decide(conn, {"ZZQ MARKET EAST": EATING})  # one disagreement of 50 needed
    categorise.run(conn, 1)
    assert _suspensions(conn) == 0
    monkeypatch.setattr(auto, "MIN_DECISIONS", 2)
    _decide(conn, {"ZZQ MARKET EAST": GROCERIES, "QQX NOODLE STALL": EATING})
    categorise.run(conn, 1)  # 2 of 2 agree (the latest answer counts)
    assert _suspensions(conn) == 0


def test_undo_returns_the_whole_batch_to_review_for_good(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _threshold(monkeypatch, 1)
    categorise.run(conn, 1)
    assert all(src == AUTO for n in GUESSED for _, src in _by_merchant(conn)[n])
    before = Ledger(conn, 1).ledger_version()
    assert auto.undo(conn, 1) == len(GUESSED)
    assert Ledger(conn, 1).ledger_version() > before
    for _ in range(2):  # and a later run does not apply them again
        got = _by_merchant(conn)
        for n in GUESSED:
            assert got[n] == {(UNCAT, "default")}
        categorise.run(conn, 1)
    assert auto.undo(conn, 1) == 0


def test_the_api_undoes_the_latest_auto_batch_in_one_call(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from finance_agent import api

    with engine.begin() as c:
        _setup(c, tmp_path)
        _threshold(monkeypatch, 1)
        categorise.run(c, 1)
    with TestClient(api.create_app(engine)) as client:
        r = client.post("/api/review-queue/undo-auto")
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == len(GUESSED)
        got = {i["title"] for i in body["items"] if i["type"] == "category"}
        assert got == set(GUESSED)
        r = client.post("/api/review-queue/undo-auto")
        assert (r.json()["applied"], r.json()["message"]) == (0, "Nothing to undo.")


def test_a_correction_beats_an_auto_applied_category(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _threshold(monkeypatch, _floor(conn))
    categorise.run(conn, 1)
    led = Ledger(conn, 1)
    row = next(
        t.id
        for t in led.transactions()
        if t.categorisation and t.categorisation.merchant == "ZZQ MARKET WEST"
    )
    led.correct_transaction(row, EATING)
    categorise.run(conn, 1)
    c = {t.id: t.categorisation for t in Ledger(conn, 1).transactions()}[row]
    assert c is not None and (c.category, c.source) == (EATING, "correction")


def test_suspension_is_per_model_version(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auto, "MIN_DECISIONS", 2)
    _threshold(monkeypatch, _floor(conn))
    categorise.run(conn, 1)
    _decide(conn, {"ZZQ MARKET EAST": EATING, "QQX NOODLE STALL": GROCERIES})
    categorise.run(conn, 1)
    assert _suspensions(conn) == 1
    _threshold(monkeypatch, 1)
    conn.execute(
        text("UPDATE merchant_embedding SET model = :m"), {"m": f"{MODEL}@new"}
    )  # a re-pulled model: a new version with its own record
    categorise.run(conn, 1)
    assert {src for _, src in _by_merchant(conn)[ODD]} == {f"{AUTO}@new"}


def test_a_run_is_deterministic(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _threshold(monkeypatch, _floor(conn))
    categorise.run(conn, 1)
    first = _by_merchant(conn)
    categorise.run(conn, 1)
    assert _by_merchant(conn) == first


def test_after_a_sync_new_vectors_apply_at_once_and_failures_stay_quiet(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with engine.begin() as c:
        _import(c, tmp_path, 1, _rows())
        _decide(c, LABELLED)

    def embed(eng: Engine, user_id: int) -> None:
        with eng.begin() as c:
            embedding.refresh(c, user_id, TrigramEmbedder())

    monkeypatch.setattr(embedding, "refresh_quietly", embed)
    _threshold(monkeypatch, 1)
    auto.refresh_quietly(engine, 1)  # what import and sync call
    with engine.begin() as c:
        assert set(_scores(c)) == set(GUESSED)
        assert all(src == AUTO for n in GUESSED for _, src in _by_merchant(c)[n])

    def boom(*_: object) -> int:
        raise RuntimeError("synthetic")

    monkeypatch.setattr(auto, "apply", boom)
    auto.refresh_quietly(engine, 1)  # never raises


# --- property ---------------------------------------------------------------

CATS = [GROCERIES, EATING, ("Shopping", "Online")]


@settings(max_examples=12, deadline=None)
@given(
    threshold=st.integers(0, OFF),
    undo_first=st.booleans(),
    decisions=st.dictionaries(st.sampled_from(GUESSED), st.sampled_from(CATS)),
    row_fixes=st.dictionaries(st.sampled_from(GUESSED), st.sampled_from(CATS)),
)
def test_nothing_applies_below_threshold_corrections_win_undone_never_return(
    threshold: int,
    undo_first: bool,
    decisions: dict[str, tuple[str, str]],
    row_fixes: dict[str, tuple[str, str]],
) -> None:
    with pytest.MonkeyPatch.context() as mp, tempfile.TemporaryDirectory() as d:
        mp.setattr(auto, "MIN_DECISIONS", 2)
        mp.setitem(auto.THRESHOLDS, MODEL, threshold)
        eng = open_db(Path(d) / "db" / "ledger.db")
        try:
            with eng.begin() as conn:
                _setup(conn, Path(d))
                undone: set[str] = set()
                if undo_first:
                    names = {i: n for n, i in _ids(conn).items()}
                    undone = {
                        names[m]
                        for m in conn.execute(
                            text(
                                "SELECT merchant_id FROM category_prediction"
                                " WHERE batch = (SELECT max(batch)"
                                " FROM category_prediction)"
                            )
                        ).scalars()
                    }
                    auto.undo(conn, 1)
                _decide(conn, decisions)
                led = Ledger(conn, 1)
                fixed: dict[int, tuple[str, tuple[str, str]]] = {}
                for t in led.transactions():  # one row of each Merchant
                    c = t.categorisation
                    m = c.merchant if c else None
                    if m in row_fixes and m not in {n for n, _ in fixed.values()}:
                        led.correct_transaction(t.id, row_fixes[m])
                        fixed[t.id] = (m, row_fixes[m])
                categorise.run(conn, 1)
                categorise.run(conn, 1)
                scores = _scores(conn)
                for t in Ledger(conn, 1).transactions():
                    c = t.categorisation
                    assert c is not None
                    if t.id in fixed:
                        assert (c.category, c.source) == (fixed[t.id][1], "correction")
                    elif c.merchant in decisions:
                        assert c.source == "correction"
                    if c.source.startswith("auto:"):
                        assert c.merchant is not None
                        assert c.merchant not in undone
                        assert scores[c.merchant] >= threshold
        finally:
            eng.dispose()
