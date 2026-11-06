"""#27 / ADR 0013: the test suite's egress guard allows only localhost Ollama and
the one SimpleFIN host. No test here connects anywhere."""

import socket
import urllib.error
import urllib.request

import pytest

from conftest import ALLOWED_EGRESS, EgressBlocked
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
