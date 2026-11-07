"""#113: the local embedding adapter, vector storage and deletion. Ollama is
replaced by a stand-in connection or `TrigramEmbedder`, so CI never needs it.
Synthetic descriptors only."""

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import Connection, Engine, text

from finance_agent import categorise
from finance_agent.categorise import embedding
from finance_agent.categorise.embedding import (
    EmbeddingUnavailable,
    OllamaEmbedder,
    OllamaHostRefused,
    TrigramEmbedder,
)
from finance_agent.ledger import Ledger
from finance_agent.storage import schema
from test_review_queue import export

CAFE, GYM, SHOP = "SYNTH CAFE", "SYNTH GYM", "SYNTH SHOP"


# --- a stand-in for http.client ---------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, replies: list[tuple[int, Any]] | Exception) -> None:
        self.replies = replies
        self.sent: list[tuple[str, str, Any]] = []
        self.closed = 0

    def __call__(self) -> "FakeConnection":
        return self

    def request(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        headers: Mapping[str, str] = {},  # noqa: B006
    ) -> None:
        if isinstance(self.replies, Exception):
            raise self.replies
        self.sent.append((method, url, json.loads(body) if body else None))

    def getresponse(self) -> FakeResponse:
        assert isinstance(self.replies, list)
        status, body = self.replies.pop(0)
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        return FakeResponse(status, raw)

    def close(self) -> None:
        self.closed += 1


def ollama(
    replies: list[tuple[int, Any]] | Exception,
) -> tuple[OllamaEmbedder, FakeConnection]:
    fake = FakeConnection(replies)
    return OllamaEmbedder(connect=fake), fake


# --- host pinning -------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://127.0.0.1:11434/", "http://[::1]:11434"]
)
def test_only_local_11434_is_accepted(url: str) -> None:
    OllamaEmbedder(base_url=url)


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost:11434",
        "http://localhost:8080",
        "http://example.com:11434",
        "http://localhost.example.com:11434",
        "http://user:pw@localhost:11434",
        "http://localhost:11434/v1",
        "http://localhost:11434?x=1",
        "http://[::1:11434",
        "http://192.168.1.2:11434",
    ],
)
def test_any_other_endpoint_is_refused(url: str) -> None:
    with pytest.raises(OllamaHostRefused):
        OllamaEmbedder(base_url=url)


def test_the_real_connection_dials_localhost_11434() -> None:
    conn = OllamaEmbedder().open_connection()
    assert (conn.host, conn.port) == ("localhost", 11434)


# --- the adapter ----------------------------------------------------------------


def test_embed_asks_for_the_model_with_a_one_minute_keep_alive() -> None:
    e, fake = ollama([(200, {"embeddings": [[0.5, -0.25], [0.0, 1.0]]})])
    assert e.embed([CAFE, GYM]) == [(32767, -16384), (0, 32767)]
    assert fake.sent == [
        (
            "POST",
            "/api/embed",
            {"model": "nomic-embed-text", "input": [CAFE, GYM], "keep_alive": "1m"},
        )
    ]
    assert fake.closed == 1


def test_embedding_nothing_never_calls_ollama() -> None:
    e, fake = ollama(ConnectionRefusedError())
    assert e.embed([]) == []
    assert fake.sent == []


MALFORMED: list[list[tuple[int, Any]] | Exception] = [
    ConnectionRefusedError(),  # Ollama not running or not installed
    [(404, {"error": "model not found, try pulling it first"})],
    [(302, b"")],  # a redirect is refused, never followed
    [(500, b"")],
    [(200, b"not json")],
    [(200, {"embeddings": [[0.1, 0.2]]})],  # one vector for two inputs
    [(200, {"embeddings": [[0.1, 0.2], [0.1]]})],  # ragged
    [(200, {"embeddings": [[0.1, "x"], [0.1, 0.2]]})],
    [(200, {"embeddings": [[True, 0.2], [0.1, 0.2]]})],
    [(200, {"embeddings": [[], []]})],
    [(200, b'{"embeddings": [[NaN, 1], [1, 1]]}')],
    [(200, {"embeddings": [[0.1] * 5000, [0.1] * 5000]})],
    [(200, ["embeddings"])],
]


@pytest.mark.parametrize("replies", MALFORMED)
def test_down_or_malformed_ollama_is_unavailable_and_quotes_nothing(
    replies: list[tuple[int, Any]] | Exception,
) -> None:
    e, _ = ollama(replies)
    with pytest.raises(EmbeddingUnavailable) as err:
        e.embed([CAFE, GYM])
    assert CAFE not in str(err.value) and "SYNTH" not in str(err.value)


HOSTILE: list[bytes] = [
    b"[" * 100_000 + b"]" * 100_000,  # nested past the parser's recursion limit
    b'{"embeddings": [[1e999999999, 1], [1, 1]]}',  # finite, overflows scaling
    b'{"embeddings": [[Infinity, 1], [1, 1]]}',
    b'{"embeddings": [[' + b"9" * 5000 + b", 1], [1, 1]]}",  # past int digit limit
    b'{"embeddings": [[1, 1], [1, 1], [1, 1]]}',  # more vectors than inputs
    b'{"embeddings": [[[1], [1]], [[1], [1]]]}',
    b'{"embeddings": null}',
]


@pytest.mark.parametrize("body", HOSTILE)
def test_a_hostile_reply_is_unavailable_never_a_crash(body: bytes) -> None:
    """Verifier #189: anything on :11434 may answer; the rules must carry on."""
    e, _ = ollama([(200, body)])
    with pytest.raises(EmbeddingUnavailable):
        e.embed([CAFE, GYM])


@pytest.mark.parametrize(
    "url",
    [
        "http://0.0.0.0:11434",
        "http://[::ffff:127.0.0.1]:11434",
        "http://127.0.0.2:11434",
        "http://localhost:11434@evil.example:11434",
        "http://127.0.0.1.nip.io:11434",
        "http://[::]:11434",
    ],
)
def test_host_tricks_are_refused(url: str) -> None:
    with pytest.raises(OllamaHostRefused):
        OllamaEmbedder(base_url=url)


def test_ollama_host_and_proxy_env_vars_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for var in ("OLLAMA_HOST", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://evil.example:8080")
    conn = OllamaEmbedder().open_connection()
    assert (conn.host, conn.port) == ("localhost", 11434)
    assert getattr(conn, "_tunnel_host", None) is None  # no CONNECT proxy


def test_digest_is_the_pulled_models_version_and_none_when_off() -> None:
    tags = {
        "models": [
            {"name": "other:latest", "digest": "aa"},
            {"name": "nomic-embed-text:latest", "digest": "bb"},
        ]
    }
    e, fake = ollama([(200, tags)])
    assert e.digest() == "bb"
    assert fake.sent == [("GET", "/api/tags", None)]  # lists models, loads none
    assert (
        ollama([(200, {"models": [{"name": "other", "digest": "aa"}]})])[0].digest()
        is None
    )
    assert ollama([(200, {"models": "x"})])[0].digest() is None
    assert ollama(ConnectionRefusedError())[0].digest() is None


@given(
    st.lists(st.decimals(-10, 10, allow_nan=False, places=6), min_size=1, max_size=40)
)
def test_quantised_vectors_fit_int16_and_keep_the_largest_at_full_scale(
    values: list[Decimal],
) -> None:
    q = embedding.quantise(values)
    assert len(q) == len(values)
    assert all(-32767 <= x <= 32767 for x in q)
    if any(values):
        assert max(abs(x) for x in q) == 32767


@given(
    st.lists(st.integers(-32767, 32767), min_size=1, max_size=30),
    st.integers(1, 9),
)
def test_similarity_is_bounded_symmetric_and_scale_free(v: list[int], k: int) -> None:
    w = list(reversed(v))
    s = embedding.similarity(tuple(v), tuple(w))
    assert -1000 <= s <= 1000
    assert s == embedding.similarity(tuple(w), tuple(v))
    if any(v):
        assert embedding.similarity(tuple(v), tuple(v)) >= 999
        big = tuple(x * k for x in v)
        assert embedding.similarity(big, tuple(v)) >= 999


def test_trigrams_are_deterministic_and_close_for_close_names() -> None:
    a, b, c = TrigramEmbedder().embed(
        ["BEAN THERE COFFEE", "BEAN HERE COFFEE", "SKYWING AIR"]
    )
    assert TrigramEmbedder().embed(["BEAN THERE COFFEE"]) == [a]
    assert embedding.similarity(a, b) > 700 > embedding.similarity(a, c)


# --- storage and deletion ---------------------------------------------------------


class Counting(TrigramEmbedder):
    def __init__(self) -> None:
        self.seen: list[list[str]] = []

    def embed(self, texts: Sequence[str]) -> list[embedding.Vector]:
        self.seen.append(list(texts))
        return super().embed(texts)


class Down:
    model = "nomic-embed-text"

    def embed(self, texts: Sequence[str]) -> list[embedding.Vector]:
        raise EmbeddingUnavailable("Ollama is not running on localhost")


def _import(
    conn: Connection, tmp_path: Path, user_id: int, rows: list[tuple[str, int]]
) -> None:
    if user_id != 1:
        conn.execute(
            schema.user.insert().values(id=user_id, created_at=datetime.now(UTC))
        )
    p = tmp_path / f"u{user_id}.csv"
    p.write_text(export(rows))
    Ledger(conn, user_id).import_files([p])
    categorise.run(conn, user_id)


@pytest.fixture
def conn(engine: Engine, tmp_path: Path) -> Iterator[Connection]:
    with engine.begin() as c:
        _import(c, tmp_path, 1, [(CAFE, -350), (CAFE, -400), (GYM, -2500)])
        yield c


def _ids(conn: Connection, user_id: int = 1) -> dict[str, int]:
    return {m.name: m.id for m in Ledger(conn, user_id).merchants()}


def test_refresh_stores_one_vector_per_merchant_once(conn: Connection) -> None:
    e = Counting()
    assert embedding.refresh(conn, 1, e) == 2
    assert sorted(e.seen[0]) == [CAFE, GYM]  # the Merchant name, once each
    assert embedding.refresh(conn, 1, e) == 0
    assert e.seen[1:] == [[]]
    ids = _ids(conn)
    stored = embedding.vectors(conn, 1, e.model)
    assert stored.keys() == {ids[CAFE], ids[GYM]}
    assert stored[ids[CAFE]] == TrigramEmbedder().embed([CAFE])[0]


def test_a_new_model_replaces_the_old_vectors(conn: Connection) -> None:
    embedding.refresh(conn, 1, TrigramEmbedder())

    class Other(TrigramEmbedder):
        model = "other-v2"

    assert embedding.refresh(conn, 1, Other()) == 2
    assert embedding.vectors(conn, 1, TrigramEmbedder.model) == {}
    assert len(embedding.vectors(conn, 1, "other-v2")) == 2


def test_local_model_off_falls_back_to_rules_and_logs_no_names(
    conn: Connection, caplog: pytest.LogCaptureFixture
) -> None:
    before = conn.execute(text("SELECT * FROM categorisation ORDER BY 2")).all()
    with caplog.at_level(logging.INFO):
        assert embedding.refresh(conn, 1, Down()) is None
    assert embedding.vectors(conn, 1, Down.model) == {}
    assert conn.execute(text("SELECT * FROM categorisation ORDER BY 2")).all() == before
    assert "off" in caplog.text and "SYNTH" not in caplog.text


def test_vectors_are_user_scoped(conn: Connection, tmp_path: Path) -> None:
    _import(conn, tmp_path, 2, [(SHOP, -100)])
    embedding.refresh(conn, 1, TrigramEmbedder())
    assert embedding.refresh(conn, 2, TrigramEmbedder()) == 1
    assert embedding.vectors(conn, 2, TrigramEmbedder.model).keys() == {
        _ids(conn, 2)[SHOP]
    }
    assert embedding.forget(conn, 2) == 1
    assert len(embedding.vectors(conn, 1, TrigramEmbedder.model)) == 2


def _delete_transactions(conn: Connection, where: str) -> None:
    """What an Import or Account deletion must do (the Ledger has no such path
    yet): a Transaction's derived rows go first, as the foreign keys demand."""
    found = conn.execute(text(f'SELECT id FROM "transaction" WHERE {where}'))  # noqa: S608
    ids = ", ".join(str(i) for i in found.scalars()) or "NULL"
    for table in ("categorisation", "transaction_source", "transaction"):
        key = "id" if table == "transaction" else "transaction_id"
        conn.execute(text(f'DELETE FROM "{table}" WHERE {key} IN ({ids})'))  # noqa: S608


def test_deleting_the_last_transaction_naming_a_merchant_deletes_its_vector(
    conn: Connection,
) -> None:
    embedding.refresh(conn, 1, TrigramEmbedder())
    ids = _ids(conn)
    _delete_transactions(conn, f"description = '{CAFE}' AND amount_minor = -350")
    assert embedding.vectors(conn, 1, TrigramEmbedder.model).keys() == {
        ids[CAFE],
        ids[GYM],
    }
    _delete_transactions(conn, f"description = '{CAFE}'")
    assert embedding.vectors(conn, 1, TrigramEmbedder.model).keys() == {ids[GYM]}


def test_deleting_many_transactions_does_not_rescan_categorisation_per_row(
    conn: Connection,
) -> None:
    """Verifier #189: the trigger fires per deleted row, so each firing must be
    cheap (an index probe per vector), or an Account deletion of 10k
    Transactions takes about a minute. Counts SQLite VM steps, not seconds."""
    embedding.refresh(conn, 1, TrigramEmbedder())
    ids = _ids(conn)
    acct = conn.execute(text('SELECT account_id FROM "transaction" LIMIT 1')).scalar()
    n = 3000
    conn.execute(
        text(
            "WITH RECURSIVE i(k) AS (SELECT 1 UNION ALL SELECT k + 1 FROM i"
            ' WHERE k < :n) INSERT INTO "transaction" (user_id, account_id,'
            " posted_on, bank_type, description, amount_minor, currency, status)"
            " SELECT 1, :a, '2026-01-02', 'CARD', :d, -100000 - k, 'GBP', 'completed'"
            " FROM i"
        ),
        {"n": n, "a": acct, "d": CAFE},
    )
    conn.execute(
        text(
            "INSERT INTO categorisation (user_id, transaction_id, kind,"
            " merchant_id, source, taxonomy_version) SELECT 1, id, 'Spending',"
            " :m, 'default', 2 FROM \"transaction\" WHERE amount_minor <= -100001"
        ),
        {"m": ids[CAFE], "n": n},
    )
    half = f"amount_minor <= -{100000 + n // 2 + 1}"
    sql = f'DELETE FROM categorisation WHERE transaction_id IN (SELECT id FROM "transaction" WHERE {half})'  # noqa: E501, S608
    conn.execute(text(sql))
    raw: Any = conn.connection.driver_connection
    steps = [0]

    def tick() -> int:
        steps[0] += 1
        return 0

    raw.set_progress_handler(tick, 1000)
    try:
        conn.execute(text(f'DELETE FROM "transaction" WHERE {half}'))  # noqa: S608
    finally:
        raw.set_progress_handler(None, 0)
    assert steps[0] * 1000 < 5_000_000, steps[0]
    assert embedding.vectors(conn, 1, TrigramEmbedder.model).keys() == {
        ids[CAFE],
        ids[GYM],
    }


def test_deleting_an_import_or_account_deletes_its_vectors_only(
    conn: Connection, tmp_path: Path
) -> None:
    _import(conn, tmp_path, 2, [(SHOP, -100)])
    embedding.refresh(conn, 1, TrigramEmbedder())
    embedding.refresh(conn, 2, TrigramEmbedder())
    imp = conn.execute(
        text("SELECT id FROM statement_import WHERE user_id = 1")
    ).scalar_one()
    sourced = "SELECT transaction_id FROM transaction_source WHERE statement_import_id"
    _delete_transactions(conn, f"id IN ({sourced} = {int(imp)})")
    conn.execute(text("DELETE FROM statement_import WHERE id = :i"), {"i": imp})
    assert embedding.vectors(conn, 1, TrigramEmbedder.model) == {}
    embedding.refresh(conn, 1, TrigramEmbedder())  # nothing left to embed
    _delete_transactions(
        conn, "account_id IN (SELECT id FROM account WHERE user_id = 1)"
    )
    assert embedding.vectors(conn, 1, TrigramEmbedder.model) == {}
    assert len(embedding.vectors(conn, 2, TrigramEmbedder.model)) == 1  # untouched


def test_deleting_a_merchant_deletes_its_vector(conn: Connection) -> None:
    embedding.refresh(conn, 1, TrigramEmbedder())
    ids = _ids(conn)
    _delete_transactions(conn, f"description = '{GYM}'")  # nothing may name it
    conn.execute(
        text("INSERT INTO merchant_embedding SELECT 1, :m, 'x', 1, x'0100'"),
        {"m": ids[GYM]},
    )
    conn.execute(text("DELETE FROM merchant WHERE id = :m"), {"m": ids[GYM]})
    assert (
        conn.execute(
            text("SELECT count(*) FROM merchant_embedding WHERE merchant_id = :m"),
            {"m": ids[GYM]},
        ).scalar_one()
        == 0
    )


def test_refresh_drops_vectors_whose_merchant_names_nothing(conn: Connection) -> None:
    embedding.refresh(conn, 1, TrigramEmbedder())
    ids = _ids(conn)
    conn.execute(
        text("DELETE FROM categorisation WHERE merchant_id = :m"), {"m": ids[GYM]}
    )
    embedding.refresh(conn, 1, TrigramEmbedder())
    assert embedding.vectors(conn, 1, TrigramEmbedder.model).keys() == {ids[CAFE]}


def test_forget_wipes_every_vector_and_categorisation_survives(
    conn: Connection,
) -> None:
    embedding.refresh(conn, 1, TrigramEmbedder())
    before = conn.execute(text("SELECT count(*) FROM categorisation")).scalar_one()
    assert embedding.forget(conn, 1) == 2
    assert (
        conn.execute(text("SELECT count(*) FROM merchant_embedding")).scalar_one() == 0
    )
    assert (
        conn.execute(text("SELECT count(*) FROM categorisation")).scalar_one() == before
    )
    assert embedding.forget(conn, 1) == 0


# --- the API status and `fa forget-embeddings` ----------------------------------


class Pulled:
    def digest(self) -> str | None:
        return "bb"


class NotPulled:
    def digest(self) -> str | None:
        return None


@pytest.mark.parametrize(("probe", "status"), [(Pulled(), "on"), (NotPulled(), "off")])
def test_api_says_whether_the_local_model_is_on(
    engine: Engine, probe: Any, status: str
) -> None:
    from fastapi.testclient import TestClient

    from finance_agent import api
    from finance_agent.api import local_model

    app = api.create_app(engine)
    app.dependency_overrides[local_model.probe] = lambda: probe
    with TestClient(app) as client:
        assert client.get("/api/local-model").json() == {"status": status}


def test_the_default_probe_is_the_localhost_ollama_adapter() -> None:
    from finance_agent.api import local_model

    assert isinstance(local_model.probe(), OllamaEmbedder)


def test_fa_forget_embeddings_wipes_them_and_never_creates_a_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from finance_agent.cli import main
    from finance_agent.storage import open_db

    path = tmp_path / "ledger.db"
    monkeypatch.setenv("FA_DB_PATH", str(path))
    assert main(["forget-embeddings"]) == 1
    assert not path.exists()
    eng = open_db(path)
    with eng.begin() as c:
        _import(c, tmp_path, 1, [(CAFE, -350), (GYM, -2500)])
        embedding.refresh(c, 1, TrigramEmbedder())
    capsys.readouterr()
    assert main(["forget-embeddings"]) == 0
    out = capsys.readouterr().out
    assert "2" in out and "SYNTH" not in out
    with eng.begin() as c:
        count = "SELECT count(*) FROM {}"
        assert c.execute(text(count.format("merchant_embedding"))).scalar_one() == 0
        assert c.execute(text(count.format("categorisation"))).scalar_one() == 2
    eng.dispose()
