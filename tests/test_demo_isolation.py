"""The demo (#296): one visitor never reaches another's ledger, the Keychain or
the network, and its figures are the Ledger's."""

import json
import os
import re
import shutil
import socket
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import keyring
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine

from build_demo_db import build as build_personas
from finance_agent import storage
from finance_agent.api import create_app
from finance_agent.demo import app as demo
from finance_agent.demo import guard, session
from finance_agent.demo.session import Visitor, Visitors, plain_engine, sign
from finance_agent.ledger import Ledger
from finance_agent.storage import keychain

SECRET = "s" * 40
N = 3  # Personas: fewer and shorter than a deploy's six, to keep the suite quick
HOST = "https://testserver"


@pytest.fixture(scope="module")
def personas(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Six Persona ledgers, built once by the deploy script."""
    held = keyring.get_keyring()
    root = tmp_path_factory.mktemp("personas")
    build_personas(root, histories=N, months=14)
    keyring.set_keyring(held)
    yield root / "personas"


@pytest.fixture
def demo_dir(tmp_path: Path, personas: Path) -> Path:
    root = tmp_path / "demo"
    shutil.copytree(personas, root / "personas")
    return root


@pytest.fixture
def app(demo_dir: Path) -> Iterator[FastAPI]:
    held = keyring.get_keyring()  # the demo replaces it process-wide
    env = {"FA_DEMO": "1", "FA_DEMO_DIR": str(demo_dir), "FA_DEMO_SECRET": SECRET}
    yield demo.build(env)
    keyring.set_keyring(held)


def client(app: FastAPI, v: Visitor | None = None) -> TestClient:
    c = TestClient(app, base_url=HOST, headers={"origin": HOST})
    if v is not None:
        c.cookies.set(session.COOKIE, sign(SECRET.encode(), v))
    return c


def visitor_of(r: Response) -> Visitor:
    """The visitor a response's Set-Cookie names."""
    v = session.verify(SECRET.encode(), r.cookies.get(session.COOKIE), N)
    assert v is not None
    return v


A = Visitor("a" * 32, 0)
B = Visitor("b" * 32, 0)  # the same Persona, so only isolation keeps them apart


def first_id(c: TestClient) -> int:
    r = c.get("/api/transactions", params={"limit": 1})
    assert r.status_code == 200
    return r.json()["items"][0]["id"]


def note(c: TestClient, txn: int) -> str | None:
    r = c.get(f"/api/notes/{txn}")
    return r.json()["text"] if r.status_code == 200 else None


def test_a_visitors_write_never_reaches_another(app: FastAPI) -> None:
    a, b = client(app, A), client(app, B)
    txn = first_id(a)
    assert first_id(b) == txn  # same Persona, same ids: the files are the guard
    assert (
        a.put(f"/api/notes/{txn}", json={"text": "visitor A only"}).status_code == 200
    )
    assert note(a, txn) == "visitor A only"
    assert note(b, txn) is None
    assert "visitor A only" not in b.get("/api/transactions").text


FORGES: list[Callable[[str], str]] = [
    lambda c: c.replace(A.id, B.id, 1),  # another id under A's signature
    lambda c: c[:-1] + ("0" if c[-1] != "0" else "1"),  # a flipped MAC
    lambda c: c.rsplit(".", 1)[0],  # no MAC
    lambda c: f"{B.id}.0.{'0' * 64}",
    lambda c: sign(b"another secret, forty characters long!!", B),
    lambda c: sign(SECRET.encode(), Visitor("../../" + "b" * 26, 0)),
    lambda c: sign(SECRET.encode(), Visitor(B.id.upper(), 0)),
    lambda c: f"{c}.{c}",
    lambda c: c.replace(".0.", ".1.", 1),  # another Persona under A's signature
    lambda c: "",
]


@pytest.mark.parametrize("forge", FORGES)
def test_a_forged_cookie_gets_a_fresh_session(
    app: FastAPI, forge: Callable[[str], str]
) -> None:
    a, b = client(app, A), client(app, B)
    txn = first_id(b)
    b.put(f"/api/notes/{txn}", json={"text": "visitor B only"})
    good = sign(SECRET.encode(), A)
    m = client(app)
    m.cookies.set(session.COOKIE, forge(good))
    r = m.get("/api/transactions")
    fresh = visitor_of(r)
    assert fresh.id not in (A.id, B.id)
    assert "visitor B only" not in r.text
    assert note(m, txn) is None
    assert a.get("/api/health").status_code == 200


def test_new_visitors_get_secure_cookies_and_their_own_file(
    app: FastAPI, demo_dir: Path
) -> None:
    c = client(app)
    r = c.get("/api/health")
    cookie = r.headers["set-cookie"]
    for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
        assert flag in cookie
    v = visitor_of(r)
    assert (demo_dir / "visitors" / f"{v.id}.db").is_file()
    assert "set-cookie" not in c.get("/api/health").headers  # already valid


def test_personas_differ(app: FastAPI) -> None:
    rows = {
        p: client(app, Visitor(f"{p:032x}", p)).get("/api/transactions").json()
        for p in range(N)
    }
    assert len({json.dumps(r) for r in rows.values()}) == N
    accounts = client(app, A).get("/api/accounts").text
    assert "Demo Bank" in accounts
    assert not any(b in accounts for b in ("Lloyds", "HSBC", "Revolut"))


def test_reset_and_shuffle(app: FastAPI, demo_dir: Path) -> None:
    a = client(app, A)
    txn = first_id(a)
    a.put(f"/api/notes/{txn}", json={"text": "gone after reset"})
    assert a.post("/api/demo/reset").json() == {"persona": 0}
    assert note(a, txn) is None
    r = a.post("/api/demo/shuffle")
    new = visitor_of(r)
    assert r.json() == {"persona": new.persona} and new.persona != 0
    assert new.id != A.id
    assert not (demo_dir / "visitors" / f"{A.id}.db").exists()


def test_eviction_keeps_200_and_drops_the_least_recently_used(demo_dir: Path) -> None:
    vs = Visitors(guard.inside(demo_dir, demo_dir))
    made = [Visitor(f"{i:032x}", i % N) for i in range(201)]
    for i, v in enumerate(made):
        p = vs.open(v)
        os.utime(p, ns=(i * 10**9, i * 10**9))  # deterministic order
    files = {p.stem for p in (demo_dir / "visitors").glob("*.db")}
    assert len(files) == 200 and made[0].id not in files
    os.utime(vs.open(made[1]))  # used again: now the newest
    vs.open(Visitor("f" * 32, 0))
    files = {p.stem for p in (demo_dir / "visitors").glob("*.db")}
    assert made[1].id in files and made[2].id not in files and len(files) == 200


def test_201_cookieless_visitors_leave_200_files(app: FastAPI, demo_dir: Path) -> None:
    first = client(app)
    oldest = visitor_of(first.get("/api/health"))
    os.utime(demo_dir / "visitors" / f"{oldest.id}.db", ns=(0, 0))
    for _ in range(200):
        client(app).get("/api/health")
    files = list((demo_dir / "visitors").glob("*.db"))
    assert len(files) == 200
    assert oldest.id not in {f.stem for f in files}


DISABLED = [
    ("GET", "/api/card-art/visa"),
    ("POST", "/api/sync"),
    ("GET", "/api/sync-status"),
    ("POST", "/api/connect"),
    ("POST", "/api/import"),
    ("GET", "/api/local-model"),
    ("GET", "/api/chat/model"),
    ("PUT", "/api/chat/model"),
    ("POST", "/api/chat/warm"),
    ("GET", "/api/backup"),
    ("POST", "/api/gateway/rewrite"),
    ("GET", "/api/cloud/spend"),
    ("GET", "/api/preferences"),  # M6 writes: off in the demo
    ("POST", "/api/preferences/answer"),
    ("DELETE", "/api/preferences"),
    ("GET", "/openapi.json"),
    ("GET", "/api/summary-and-more"),
    ("GET", "/api"),
]


@pytest.mark.parametrize(("method", "path"), DISABLED)
def test_disabled_routes_are_404(app: FastAPI, method: str, path: str) -> None:
    assert client(app).request(method, path).status_code == 404


def test_every_api_route_off_the_allowlist_is_404(app: FastAPI) -> None:
    """Fail closed: the local API's whole schema, swept."""
    full = create_app(cast(Engine, None)).openapi()["paths"]
    off = [(m, p) for p, ms in full.items() for m in ms if not demo.allowed(p)]
    assert {"/api/card-art/{key}", "/api/sync", "/api/local-model"} <= {
        p for _, p in off
    }
    c = client(app)
    for method, path in off:
        url = re.sub(r"{[^}]+}", "1", path)
        assert c.request(method.upper(), url).status_code == 404, (method, path)


@pytest.mark.parametrize(
    "path", ["/api/goals", "/api/guidance", "/api/plan/allocation", "/api/search"]
)
def test_m6_read_routes_answer_in_the_demo(app: FastAPI, path: str) -> None:
    r = client(app, A).get(path, params={"q": "synthetic"})
    assert r.status_code == 200, (path, r.text)


def test_a_root_path_cannot_smuggle_a_disabled_route(app: FastAPI) -> None:
    c = TestClient(app, base_url=HOST, headers={"origin": HOST}, root_path="/x")
    assert c.get("/x/api/card-art/visa").status_code == 404
    assert c.post("/x/api/sync").status_code == 404


@pytest.mark.parametrize(
    "origin", ["https://evil.example", "null", "https://testserver.evil", None, ""]
)
def test_a_write_from_another_origin_is_403(app: FastAPI, origin: str | None) -> None:
    c = client(app, A)
    txn = first_id(c)
    headers = {"origin": origin} if origin is not None else {}
    c.headers.pop("origin")
    r = c.put(f"/api/notes/{txn}", json={"text": "csrf"}, headers=headers)
    assert r.status_code == 403
    assert c.post("/api/demo/reset", headers=headers).status_code == 403
    assert note(client(app, A), txn) is None


def test_a_body_over_64kb_is_413(app: FastAPI) -> None:
    c = client(app, A)
    txn = first_id(c)
    big = "x" * (64 * 1024 + 1)
    assert c.put(f"/api/notes/{txn}", content=big).status_code == 413

    def chunks() -> Iterator[bytes]:  # no Content-Length: counted as it arrives
        for _ in range(65):
            yield b"x" * 1024

    assert c.put(f"/api/notes/{txn}", content=chunks()).status_code == 413


def test_chat_is_deterministic(app: FastAPI) -> None:
    r = client(app, A).post("/api/chat", json={"message": "How much did I spend?"})
    assert r.status_code == 200
    meta = r.text.split("event: meta\ndata: ", 1)[1].split("\n", 1)[0]
    assert json.loads(meta)["path"] in ("deterministic", "planner")  # no model


def test_the_demo_never_reaches_the_network(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The demo's egress allowlist is empty: not even localhost Ollama."""
    tried: list[Any] = []

    def refuse(*args: Any, **_: Any) -> Any:
        tried.append(args[:2])
        raise OSError("demo egress")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    c = client(app, A)
    month = c.get("/api/months").json()[-1]
    paths = app.openapi()["paths"]
    gets = [p for p in paths if "get" in paths[p] and "{" not in p and demo.allowed(p)]
    assert len(gets) > 20
    for p in gets:
        assert c.get(p, params={"month": month}).status_code < 500, p
    c.post("/api/chat", json={"message": "What did I spend on groceries?"})
    c.get("/api/review-queue")
    c.get("/api/search", params={"q": "groceries"})  # FTS only, no embedder
    c.post("/api/demo/shuffle")
    assert tried == []


def test_the_keychain_is_unreachable(app: FastAPI, tmp_path: Path) -> None:
    with pytest.raises(guard.KeychainRefused):
        keychain.secret(keychain.DB_SERVICE)
    with pytest.raises(guard.KeychainRefused):
        storage.open_db(tmp_path / "x.db")


def test_overview_totals_are_the_ledgers(app: FastAPI, demo_dir: Path) -> None:
    for p in range(N):
        c = client(app, Visitor(f"{p + 10:032x}", p))
        month = c.get("/api/months").json()[-2]
        got = c.get("/api/summary", params={"month": month}).json()
        engine = plain_engine(demo_dir / "personas" / f"{p}.db")
        with engine.connect() as conn:
            s = Ledger(conn, 1).summarise_month(month)
        engine.dispose()
        for k in ("money_in", "money_out", "net"):
            assert got[k] == {"minor": int(getattr(s, k).scaleb(2)), "currency": "GBP"}
        assert got["money_out"]["minor"] != 0


def test_a_cold_start_answers_in_under_two_seconds(demo_dir: Path) -> None:
    held = keyring.get_keyring()
    env = {"FA_DEMO": "1", "FA_DEMO_DIR": str(demo_dir), "FA_DEMO_SECRET": SECRET}
    started = time.perf_counter()
    try:
        r = client(demo.build(env)).get("/api/summary")
    finally:
        keyring.set_keyring(held)
    assert r.status_code == 200
    assert time.perf_counter() - started < 2


def test_landing_at_root_and_the_app_at_app_as_vercel_routes_it(
    demo_dir: Path,
) -> None:
    web = demo_dir / "web"
    web.mkdir()
    (web / "index.html").write_text("<!-- synthetic --><title>spa</title>")
    held = keyring.get_keyring()
    env = {"FA_DEMO": "1", "FA_DEMO_DIR": str(demo_dir), "FA_DEMO_SECRET": SECRET}
    c = client(demo.build(env))
    keyring.set_keyring(held)
    for path in ("/", "/app", "/app/", "/app/review/deep/link"):
        r = c.get(path)
        assert r.status_code == 200 and "<title>spa</title>" in r.text, path
    assert c.get("/api/health").json()["app"] == "finance-agent"
