"""#114: embedding suggestions in the Review Queue, below the gate. Only the
fake embedder and a stand-in Ollama connection; synthetic Merchants. A
suggestion is a button: nothing here is ever assigned."""

import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from sqlalchemy import Connection, Engine, text

from finance_agent.categorise import embedding
from finance_agent.categorise.embedding import OllamaEmbedder, TrigramEmbedder
from finance_agent.ledger import Ledger
from finance_agent.ledger.review import ReviewQueue
from test_categorise_embedding import (
    FakeConnection,
    FakeResponse,
    _import,  # pyright: ignore[reportPrivateUsage]
)
from test_review_queue import export

GROCERIES = ("Food & Drink", "Groceries")
EATING = ("Food & Drink", "Eating out")
# Merchants the rules do not know, so they stay Uncategorised
KNOWN = ["ZZQ MARKET NORTH", "ZZQ MARKET SOUTH", "QQX NOODLE BAR", "QQX NOODLE HOUSE"]
NEW = ["ZZQ MARKET EAST", "QQX NOODLE STALL", "WWV UNRELATED"]


@pytest.fixture
def conn(engine: Engine, tmp_path: Path) -> Iterator[Connection]:
    with engine.begin() as c:
        _import(c, tmp_path, 1, [(n, -500) for n in KNOWN + NEW])
        yield c


def _queue(conn: Connection) -> ReviewQueue:
    return ReviewQueue(
        conn,
        1,
        suggester=lambda targets, labelled: embedding.suggest(
            conn, 1, targets, labelled
        ),
    )


def _label(conn: Connection) -> None:
    led = Ledger(conn, 1)
    ids = {m.name: m.id for m in led.merchants()}
    for n in KNOWN[:2]:
        led.correct_merchant(ids[n], GROCERIES)
    for n in KNOWN[2:]:
        led.correct_merchant(ids[n], EATING)


def _suggestions(conn: Connection) -> dict[str, tuple[tuple[str, str], ...]]:
    return {
        i.title.upper(): i.suggestions
        for i in _queue(conn).items()
        if i.type == "category"
    }


def test_the_nearest_correction_labelled_merchants_fill_the_suggestions(
    conn: Connection,
) -> None:
    _label(conn)
    assert embedding.refresh(conn, 1, TrigramEmbedder()) is not None
    got = _suggestions(conn)
    assert got["ZZQ MARKET EAST"][0] == GROCERIES
    assert got["QQX NOODLE STALL"][0] == EATING
    assert all(len(v) <= 3 for v in got.values())


def test_no_vectors_means_no_suggestions_and_the_queue_still_works(
    conn: Connection,
) -> None:
    _label(conn)
    got = _suggestions(conn)
    assert len(got) == len(NEW) and all(v == () for v in got.values())


def test_suggestions_never_create_a_correction(conn: Connection) -> None:
    _label(conn)
    embedding.refresh(conn, 1, TrigramEmbedder())
    sql = "SELECT count(*) FROM correction"
    before = conn.execute(text(sql)).scalar()
    cats = conn.execute(text("SELECT * FROM categorisation ORDER BY 2")).all()
    q = _queue(conn)
    assert any(i.suggestions for i in q.items())
    assert conn.execute(text(sql)).scalar() == before
    assert conn.execute(text("SELECT * FROM categorisation ORDER BY 2")).all() == cats


def test_a_suggestion_does_not_change_the_item_version(conn: Connection) -> None:
    _label(conn)
    plain = {i.id: i.version for i in ReviewQueue(conn, 1).items()}
    embedding.refresh(conn, 1, TrigramEmbedder())
    assert {i.id: i.version for i in _queue(conn).items()} == plain


# --- refresh in import and sync -------------------------------------------------


def test_refresh_quietly_embeds_with_the_local_model(
    conn: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(embedding, "OllamaEmbedder", TrigramEmbedder)
    embedding.refresh_quietly(conn, 1)
    assert len(embedding.vectors(conn, 1, TrigramEmbedder.model)) == len(KNOWN + NEW)


def test_refresh_quietly_never_raises_nor_logs_names(
    conn: Connection, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class Boom(TrigramEmbedder):
        def embed(self, texts: Sequence[str]) -> list[embedding.Vector]:
            raise RuntimeError(texts[0])

    monkeypatch.setattr(embedding, "OllamaEmbedder", Boom)
    with caplog.at_level(logging.DEBUG):
        embedding.refresh_quietly(conn, 1)
    assert "RuntimeError" in caplog.text and "ZZQ" not in caplog.text


def test_import_and_watch_call_refresh_after_categorise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from finance_agent.cli import LOCAL_USER, main, watch

    calls: list[int] = []

    def spy(conn: Connection, user_id: int) -> None:
        calls.append(user_id)

    monkeypatch.setattr(embedding, "refresh_quietly", spy)
    csv = tmp_path / "a.csv"
    csv.write_text(export([(KNOWN[0], -500)]))
    assert main(["import", str(csv)]) == 0
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "b.csv").write_text(export([(KNOWN[1], -500)]))
    assert watch.run(folder, lambda t, b: None) == 0
    assert calls == [LOCAL_USER, LOCAL_USER]


# --- #189 follow-ups -------------------------------------------------------------


def test_embed_batches_requests_in_order() -> None:
    n = embedding.BATCH * 2 + 5

    class Echo(FakeConnection):
        def getresponse(self) -> FakeResponse:
            sent = len(self.sent[-1][2]["input"])
            self.replies = [(200, {"embeddings": [[1, 2, 3]] * sent})]
            return super().getresponse()

    fake = Echo([])
    out = OllamaEmbedder(connect=fake).embed([f"M{i}" for i in range(n)])
    assert len(out) == n
    sizes = [len(s[2]["input"]) for s in fake.sent]
    assert sizes == [embedding.BATCH, embedding.BATCH, 5]


def test_an_oversized_reply_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding, "MAX_REPLY", 100)
    fake = FakeConnection([(200, {"embeddings": [[1.0] * 200]})])
    with pytest.raises(embedding.EmbeddingUnavailable, match="too large"):
        OllamaEmbedder(connect=fake).embed(["x"])


def test_a_changed_model_digest_re_embeds(conn: Connection) -> None:
    class Pulled(TrigramEmbedder):
        def __init__(self, digest: str) -> None:
            self._d = digest

        def digest(self) -> str:
            return self._d

    total = len(KNOWN + NEW)
    assert embedding.refresh(conn, 1, Pulled("sha-aaaaaaaaaaaaaaaa")) == total
    assert embedding.refresh(conn, 1, Pulled("sha-aaaaaaaaaaaaaaaa")) == 0
    assert embedding.refresh(conn, 1, Pulled("sha-bbbbbbbbbbbbbbbb")) == total
    models = conn.execute(text("SELECT DISTINCT model FROM merchant_embedding")).all()
    assert len(models) == 1 and "bbbbbbbb" in models[0][0]
