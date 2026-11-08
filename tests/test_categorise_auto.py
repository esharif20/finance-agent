"""#215 B: confident embedding guesses auto-apply after a sync, but only for a
model version that passed shadow mode, only above the calibrated threshold,
undoable per batch, and a Correction always wins. Synthetic Merchants and the
trigram stand-in embedder only."""

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
MODEL = TrigramEmbedder.model
RESULTS = Path(__file__).parents[1] / "evals" / "results"


def _rows() -> list[tuple[str, int]]:
    names = [*LABELLED, *DECIDE, *LATER, ODD]
    return [(n, -500) for n in names] + [(n, -700) for n in names]


def _setup(conn: Connection, tmp_path: Path) -> None:
    _import(conn, tmp_path, 1, _rows())
    led = Ledger(conn, 1)
    ids = _ids(conn)
    for n, cat in LABELLED.items():
        led.correct_merchant(ids[n], cat)
    assert embedding.refresh(conn, 1, TrigramEmbedder()) is not None
    categorise.run(conn, 1)  # the shadow log of every Review item


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


@pytest.fixture
def easy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Promotion after two agreeing decisions, any positive score."""
    monkeypatch.setattr(auto, "MIN_DECISIONS", 2)
    monkeypatch.setitem(auto.THRESHOLDS, MODEL, 1)


def test_the_app_thresholds_are_the_committed_synthetic_calibration() -> None:
    for name in ("categoriser_baseline.json", "categoriser_ollama.json"):
        nn = json.loads((RESULTS / name).read_text())["models"]["nn"]
        model = nn["model_version"].split("@")[0]
        assert auto.THRESHOLDS[model] == nn["calibration"]["threshold"]
        assert nn["calibration"]["precision"] >= 0.95


def test_before_promotion_nothing_applies_and_each_review_item_is_logged(
    conn: Connection, easy: None
) -> None:
    got = _by_merchant(conn)
    for n in [*DECIDE, *LATER, ODD]:
        assert got[n] == {(UNCAT, "default")}
    assert set(_scores(conn)) == {*DECIDE, *LATER, ODD}
    cols = conn.execute(text("SELECT * FROM category_prediction")).keys()
    assert set(cols) == {
        "id", "user_id", "model", "merchant_id", "category_id", "score",
        "created_at", "decided_category_id", "batch", "undone_at",
    }  # fmt: skip


def test_agreeing_decisions_promote_and_then_confident_rows_auto_apply(
    conn: Connection, easy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    scores = _scores(conn)
    floor = min(scores[n] for n in [*DECIDE, *LATER])
    assert scores[ODD] < floor  # the stand-in model is unsure of it
    monkeypatch.setitem(auto.THRESHOLDS, MODEL, floor)
    _decide(conn, DECIDE)
    categorise.run(conn, 1)
    got = _by_merchant(conn)
    assert got["ZZQ MARKET WEST"] == {(GROCERIES, f"auto:{MODEL}")}
    assert got["QQX NOODLE CART"] == {(EATING, f"auto:{MODEL}")}
    assert got[ODD] == {(UNCAT, "default")}  # low confidence: still to review
    review = {i.title for i in ReviewQueue(conn, 1).items() if i.type == "category"}
    assert review == {ODD}


def test_a_disagreeing_decision_keeps_the_model_in_shadow(
    conn: Connection, easy: None
) -> None:
    _decide(conn, {"ZZQ MARKET EAST": EATING, "QQX NOODLE STALL": EATING})
    categorise.run(conn, 1)
    assert all(src == "default" for n in LATER for _, src in _by_merchant(conn)[n])
    assert conn.execute(text("SELECT count(*) FROM model_promotion")).scalar() == 0


def test_fewer_than_min_decisions_never_promote(conn: Connection) -> None:
    assert auto.MIN_DECISIONS == 50
    _decide(conn, DECIDE)
    categorise.run(conn, 1)
    assert conn.execute(text("SELECT count(*) FROM model_promotion")).scalar() == 0
    assert all(src == "default" for n in LATER for _, src in _by_merchant(conn)[n])


def _promoted(conn: Connection) -> None:
    _decide(conn, DECIDE)
    categorise.run(conn, 1)
    assert _by_merchant(conn)["ZZQ MARKET WEST"] == {(GROCERIES, f"auto:{MODEL}")}


def test_undo_returns_the_whole_batch_to_review_for_good(
    conn: Connection, easy: None
) -> None:
    _promoted(conn)
    before = Ledger(conn, 1).ledger_version()
    assert auto.undo(conn, 1) == len(LATER) + 1  # ODD too, at threshold 1
    assert Ledger(conn, 1).ledger_version() > before
    for _ in range(2):  # and a later run does not apply them again
        got = _by_merchant(conn)
        for n in [*LATER, ODD]:
            assert got[n] == {(UNCAT, "default")}
        categorise.run(conn, 1)
    assert auto.undo(conn, 1) == 0


def test_the_api_undoes_the_latest_auto_batch_in_one_call(
    engine: Engine, tmp_path: Path, easy: None
) -> None:
    from fastapi.testclient import TestClient

    from finance_agent import api

    with engine.begin() as c:
        _setup(c, tmp_path)
        _promoted(c)
    with TestClient(api.create_app(engine)) as client:
        r = client.post("/api/review-queue/undo-auto")
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == len(LATER) + 1
        assert {i["title"] for i in body["items"] if i["type"] == "category"} == {
            *LATER,
            ODD,
        }
        r = client.post("/api/review-queue/undo-auto")
        assert (r.json()["applied"], r.json()["message"]) == (0, "Nothing to undo.")


def test_a_correction_beats_an_auto_applied_category(
    conn: Connection, easy: None
) -> None:
    _promoted(conn)
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


def test_a_new_model_version_starts_in_shadow_again(
    conn: Connection, easy: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(auto.THRESHOLDS, MODEL, _scores(conn)[ODD] + 1)
    _promoted(conn)  # ODD stays below the threshold, in Review
    monkeypatch.setitem(auto.THRESHOLDS, MODEL, 1)  # the old version would apply it
    conn.execute(
        text("UPDATE merchant_embedding SET model = :m"), {"m": f"{MODEL}@new"}
    )
    categorise.run(conn, 1)
    got = _by_merchant(conn)
    assert got[ODD] == {(UNCAT, "default")}
    assert got["ZZQ MARKET WEST"] == {(GROCERIES, f"auto:{MODEL}")}  # kept
    logged = conn.execute(
        text("SELECT merchant_id FROM category_prediction WHERE model = :m"),
        {"m": f"{MODEL}@new"},
    ).scalars()
    assert list(logged) == [_ids(conn)[ODD]]


def test_a_run_is_deterministic(conn: Connection, easy: None) -> None:
    _promoted(conn)
    first = _by_merchant(conn)
    categorise.run(conn, 1)
    assert _by_merchant(conn) == first


def test_after_a_sync_new_vectors_are_logged_at_once_and_failures_stay_quiet(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with engine.begin() as c:
        _import(c, tmp_path, 1, _rows())
        _decide(c, LABELLED)

    def embed(eng: Engine, user_id: int) -> None:
        with eng.begin() as c:
            embedding.refresh(c, user_id, TrigramEmbedder())

    monkeypatch.setattr(embedding, "refresh_quietly", embed)
    auto.refresh_quietly(engine, 1)  # what import and sync call
    with engine.begin() as c:
        assert set(_scores(c)) == {*DECIDE, *LATER, ODD}

    def boom(*_: object) -> int:
        raise RuntimeError("synthetic")

    monkeypatch.setattr(auto, "apply", boom)
    auto.refresh_quietly(engine, 1)  # never raises


# --- property ---------------------------------------------------------------

NAMES = [*DECIDE, *LATER, ODD]
CATS = [GROCERIES, EATING, ("Shopping", "Online")]


@settings(max_examples=12, deadline=None)
@given(
    threshold=st.integers(0, 1001),
    min_decisions=st.integers(1, 4),
    decisions=st.dictionaries(st.sampled_from(NAMES), st.sampled_from(CATS)),
    row_fixes=st.dictionaries(st.sampled_from(NAMES), st.sampled_from(CATS)),
)
def test_nothing_auto_applies_below_threshold_or_unpromoted_and_corrections_win(
    threshold: int,
    min_decisions: int,
    decisions: dict[str, tuple[str, str]],
    row_fixes: dict[str, tuple[str, str]],
) -> None:
    with pytest.MonkeyPatch.context() as mp, tempfile.TemporaryDirectory() as d:
        mp.setattr(auto, "MIN_DECISIONS", min_decisions)
        mp.setitem(auto.THRESHOLDS, MODEL, threshold)
        eng = open_db(Path(d) / "db" / "ledger.db")
        try:
            with eng.begin() as conn:
                _setup(conn, Path(d))
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
                promoted = (
                    conn.execute(text("SELECT decisions FROM model_promotion"))
                    .scalars()
                    .all()
                )
                for t in Ledger(conn, 1).transactions():
                    c = t.categorisation
                    assert c is not None
                    if t.id in fixed:
                        assert (c.category, c.source) == (fixed[t.id][1], "correction")
                    elif c.merchant in decisions:
                        assert c.source == "correction"
                    if c.source.startswith("auto:"):
                        assert promoted and promoted[0] >= min_decisions
                        assert c.merchant is not None
                        assert scores[c.merchant] >= threshold
        finally:
            eng.dispose()
