"""#27 / ADR 0013: the test suite's egress guard allows only localhost Ollama and
the one SimpleFIN host. No test here connects anywhere."""

import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

import langsmith.utils
import pytest

from conftest import ALLOWED_EGRESS, EgressBlocked
from finance_agent.assistant import OllamaChatModel, OllamaHostRefused
from finance_agent.ingest import simplefin


def test_only_ollama_and_the_simplefin_host_are_allowlisted() -> None:
    assert {h for h, _ in ALLOWED_EGRESS} == {
        "localhost",
        "127.0.0.1",
        "::1",
        simplefin.HOST,
    }
    assert {p for h, p in ALLOWED_EGRESS if h != simplefin.HOST} == {11434}


@pytest.mark.parametrize(
    "address", [("example.com", 443), ("localhost", 8080), (simplefin.HOST, 80)]
)
def test_any_other_host_fails(address: tuple[str, int]) -> None:
    with pytest.raises(EgressBlocked):
        socket.create_connection(address, timeout=1)


def test_direct_ip_connections_are_blocked() -> None:
    with socket.socket() as s, pytest.raises(EgressBlocked):
        s.connect(("192.0.2.1", 443))  # TEST-NET-1


def test_library_http_clients_are_caught() -> None:
    with pytest.raises(urllib.error.URLError) as e:
        urllib.request.urlopen("https://example.com/", timeout=1)  # noqa: S310
    assert isinstance(e.value.reason, EgressBlocked)


# #135: the assistant may open localhost:11434 only, and LangGraph/LangSmith
# send nothing, even with tracing switched on in the environment.

PROBE = """
import socket, sys, time
attempts = []
def record(name):
    def guard(*a, **k):
        attempts.append((name, repr(a[:3])))
        raise OSError("egress blocked by probe")
    return guard
socket.getaddrinfo = record("getaddrinfo")
socket.create_connection = record("create_connection")
socket.socket.connect = record("connect")
socket.socket.connect_ex = record("connect_ex")

import finance_agent.assistant
from typing import TypedDict
from langgraph.graph import StateGraph, START, END

class S(TypedDict):
    n: int

g = StateGraph(S)
g.add_node("inc", lambda s: {"n": s["n"] + 1})
g.add_edge(START, "inc")
g.add_edge("inc", END)
assert g.compile().invoke({"n": 1}) == {"n": 2}
time.sleep(1.5)  # give any background tracer thread time to try
print(attempts)
sys.exit(1 if attempts else 0)
"""


# Verifier (#164): langgraph imported and a graph compiled before the assistant
# (as another module might), runs twice through invoke and stream, then a wait
# for delayed telemetry; DNS, UDP and TLS hooks too.
LATE_PROBE = """
import socket, ssl, sys, time
attempts = []
def record(name):
    def guard(*a, **k):
        attempts.append((name, repr(a[:3])))
        raise OSError("egress blocked by probe")
    return guard
for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
    setattr(socket, n, record(n))
for n in ("connect", "connect_ex", "sendto", "sendmsg"):
    setattr(socket.socket, n, record(n))
ssl.SSLContext.wrap_socket = record("ssl.wrap_socket")

from typing import TypedDict
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph, START, END

class S(TypedDict):
    n: int

g = StateGraph(S)
g.add_node("inc", RunnableLambda(lambda s: {"n": s["n"] + 1}))
g.add_edge(START, "inc")
g.add_edge("inc", END)
app = g.compile()
import finance_agent.assistant
for _ in range(2):
    assert app.invoke({"n": 1}) == {"n": 2}
    assert list(app.stream({"n": 1}))
time.sleep(3)
print(attempts)
sys.exit(1 if attempts else 0)
"""


# #139: the real Assistant graph (router, a sub-graph, checkpoints in the
# database) on FakeChatModel and a throwaway SQLite file, never the Keychain.
GRAPH_PROBE = """
import socket, ssl, sys, time, tempfile, datetime
attempts = []
def record(name):
    def guard(*a, **k):
        attempts.append((name, repr(a[:3])))
        raise OSError("egress blocked by probe")
    return guard
for n in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
    setattr(socket, n, record(n))
for n in ("connect", "connect_ex", "sendto", "sendmsg"):
    setattr(socket.socket, n, record(n))
ssl.SSLContext.wrap_socket = record("ssl.wrap_socket")

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from finance_agent import storage
from finance_agent.assistant import FakeChatModel, Reply
from finance_agent.assistant.graph import Assistant, ask

eng = create_engine(f"sqlite:///{tempfile.mkdtemp()}/probe.db")
cfg = Config()
cfg.set_main_option("script_location", str(storage._MIGRATIONS))
with eng.begin() as c:
    cfg.attributes["connection"] = c
    command.upgrade(cfg, "head")
for script in (["plan"], ["ask", "No figures here."]):
    with eng.begin() as c:
        m = FakeChatModel([Reply(content=s) for s in script])
        a = ask(Assistant(m, c, datetime.date(2026, 1, 1)), "hello")
        assert a.path in ("deterministic", "local_model"), a
time.sleep(3)
print(attempts)
sys.exit(1 if attempts else 0)
"""


@pytest.mark.parametrize(
    "probe", [PROBE, LATE_PROBE, GRAPH_PROBE], ids=["first", "late", "graph"]
)
def test_assistant_import_and_a_graph_run_open_no_socket(probe: str) -> None:
    env = {
        **os.environ,
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_API_KEY": "lsv2_synthetic_not_a_key",
        "LANGSMITH_ENDPOINT": "http://tracer.invalid",
        "LANGSMITH_OTEL_ENABLED": "true",
    }
    run = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], env=env, capture_output=True, text=True
    )
    assert run.returncode == 0, run.stdout + run.stderr


def test_assistant_switches_langsmith_tracing_off() -> None:
    assert langsmith.utils.tracing_is_enabled() is False  # pyright: ignore[reportUnknownMemberType]
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_the_ollama_adapter_cannot_be_pointed_elsewhere() -> None:
    with pytest.raises(OllamaHostRefused):
        OllamaChatModel(base_url="http://example.com:11434")
    conn = OllamaChatModel().open_connection()
    assert (conn.host, conn.port) == ("localhost", 11434)
