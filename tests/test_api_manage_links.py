"""GET /api/manage-links (#174): the built-in list, https on its own domain."""

from collections.abc import Iterator
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from finance_agent import api
from finance_agent.categorise.manage_links import MANAGE_LINKS


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    with TestClient(api.create_app(engine)) as c:
        yield c


def test_serves_the_built_in_list(client: TestClient) -> None:
    got = client.get("/api/manage-links").json()
    assert [(e["names"], e["domain"], e["url"]) for e in got] == [
        (list(e.names), e.domain, e.url) for e in MANAGE_LINKS
    ]
    assert any("Netflix" in e["names"] for e in got)


def test_every_url_is_https_on_its_own_domain(client: TestClient) -> None:
    items: list[dict[str, str]] = client.get("/api/manage-links").json()
    for e in items:
        u = urlsplit(e["url"])
        assert u.scheme == "https"
        assert u.hostname == e["domain"] or (u.hostname or "").endswith(
            "." + e["domain"]
        )


def test_it_is_read_only(client: TestClient) -> None:
    assert client.post("/api/manage-links", json={}).status_code == 405
    assert client.delete("/api/manage-links").status_code == 405
