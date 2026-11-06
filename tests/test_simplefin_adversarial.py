"""#27 verifier: hostile provider responses and a hostile HTTP client. Nothing
here reaches the network."""

import json
import ssl
import traceback
import urllib.request
from datetime import date
from typing import Any, cast

import keyring
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from conftest import MemoryKeyring
from finance_agent.ingest import simplefin
from finance_agent.ingest.simplefin import SimpleFINError, UnreadableResponse
from test_simplefin_ingest import (
    FakeTransport,
    account,
    response,
    secret_url,
    token_for,
    txn,
)


def chain_text(err: BaseException) -> str:
    """repr and traceback of the exception and everything it chains to, even
    suppressed context that a log handler or error reporter could still walk."""
    out, seen = "", set[int]()
    e: BaseException | None = err
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        out += repr(e) + repr(e.args) + "".join(traceback.format_exception(e))
        out += repr(getattr(e, "object", ""))  # UnicodeDecodeError keeps the bytes
        e = e.__cause__ or e.__context__
    return out


class LeakyClient:
    """An HTTP client that fails with the URL and headers in its message."""

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        raise OSError(f"{method} {url} failed; headers={headers}")


def test_a_failing_http_client_leaks_nothing_even_in_suppressed_context() -> None:
    access = secret_url()
    claim_url = f"https://{simplefin.HOST}/claim/{'c' * 24}"
    secrets_ = [access, claim_url, access.split("@")[0].split("//")[1]]
    errors: list[BaseException] = []
    for call in (
        lambda: simplefin.claim(token_for(claim_url), LeakyClient()),
        lambda: simplefin.fetch(
            access, date(2026, 1, 1), date(2026, 1, 2), LeakyClient()
        ),
    ):
        with pytest.raises(SimpleFINError) as e:
            call()
        errors.append(e.value)
    for err in errors:
        text = chain_text(err)
        for s in secrets_:
            assert s not in text


def test_a_non_ascii_token_or_access_reply_leaks_nothing() -> None:
    claim_url = f"https://{simplefin.HOST}/claim/é{'c' * 24}"
    with pytest.raises(SimpleFINError) as e:
        simplefin.claim(token_for(claim_url), FakeTransport())
    assert "claim/" not in chain_text(e.value)
    access = secret_url() + "é"
    with pytest.raises(SimpleFINError) as e:
        simplefin.claim(
            token_for(f"https://{simplefin.HOST}/claim/x"),
            FakeTransport((200, access.encode())),
        )
    assert access.split("@")[0] not in chain_text(e.value)


def test_a_bad_port_in_the_access_url_never_quotes_it() -> None:
    access = secret_url().replace(simplefin.HOST, simplefin.HOST + ":s3cretport")
    with pytest.raises(SimpleFINError) as e:
        simplefin.fetch(access, date(2026, 1, 1), date(2026, 1, 2), FakeTransport())
    assert "s3cretport" not in chain_text(e.value)


def test_unreadable_json_never_chains_the_response_text() -> None:
    shop = "SECRET " + "SHOP"  # built at runtime: tracebacks quote source lines
    with pytest.raises(UnreadableResponse) as e:
        simplefin.parse(b'{"accounts": [{"description": "' + shop.encode())
    assert shop not in chain_text(e.value)


def test_an_oversized_response_is_refused() -> None:
    big = b" " * (simplefin.MAX_RESPONSE_BYTES + 1)
    with pytest.raises(SimpleFINError):
        simplefin.fetch(
            secret_url(), date(2026, 1, 1), date(2026, 1, 2), FakeTransport((200, big))
        )


def test_the_real_transport_verifies_tls_and_ignores_proxies_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    seen: list[Any] = []

    class Opener:
        def open(self, req: Any, timeout: float) -> Any:
            raise OSError("stop before the network")

    def build_opener(*handlers: Any) -> Opener:
        seen.extend(handlers)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    with pytest.raises(OSError):
        simplefin.urllib_transport("GET", f"https://{simplefin.HOST}/x", {}, None)
    (proxy,) = [h for h in seen if isinstance(h, urllib.request.ProxyHandler)]
    assert proxy.proxies == {}  # type: ignore[attr-defined]
    assert any(
        h is simplefin.NoRedirect or isinstance(h, simplefin.NoRedirect) for h in seen
    )
    (https,) = [h for h in seen if isinstance(h, urllib.request.HTTPSHandler)]
    ctx = cast(ssl.SSLContext, getattr(https, "_context"))  # noqa: B009
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert simplefin.NoRedirect().redirect_request() is None


def test_tests_never_touch_the_real_keychain() -> None:
    assert isinstance(keyring.get_keyring(), MemoryKeyring)


@pytest.mark.parametrize(
    "bad",
    [
        response(account(txn(amount="1e400"))),
        response(account(txn(amount="NaN"))),
        response(account(txn(amount="Infinity"))),
        response(account(txn(amount="-0.001"))),
        response(account(txn(amount="9" * 30))),  # over a BigInteger
        response(account(balance="9" * 30)),
        response(account(txn(amount=None))),
        response(account(txn(amount=True))),
        response(account(txn(posted=10**20))),  # past datetime's range
        response(account(txn(posted=True))),
        response(account(txn(posted=1.5))),
        response(account(txn(transacted_at=10**20))),
        response(account(**{"balance-date": -1})),
        response(account(txn(pid="x" * 300))),  # over synced_key.provider_id
        response(account(id="x" * 300)),
        response(account(txn(description="A" * 10_000_000))),
        response(account(txn(payee="A" * 10_000))),
        response(account(transactions={"a": 1})),
        response(account(transactions=[1])),
        response(account(org="bank")),
        b'{"accounts": [' * 100_000,  # deep nesting
        b'{"accounts": [], "errors": ' + b"9" * 5000 + b"}",  # huge integer
        json.dumps({"accounts": [{"id": "A1"}]}).encode(),  # missing keys
    ],
)
def test_hostile_responses_are_refused_cleanly(bad: bytes) -> None:
    with pytest.raises(UnreadableResponse):
        simplefin.parse(bad)


@settings(max_examples=200, deadline=None)
@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=8),
        lambda c: (
            st.lists(c, max_size=3)
            | st.dictionaries(st.text(max_size=8), c, max_size=3)
        ),
        max_leaves=20,
    )
)
def test_any_json_parses_or_raises_unreadable_only(doc: Any) -> None:
    for shaped in (doc, {"accounts": [doc]}, {"accounts": [account(doc)]}):
        try:
            data = json.dumps(shaped, allow_nan=True).encode()
        except (TypeError, ValueError):
            continue
        try:
            simplefin.parse(data)
        except UnreadableResponse:
            pass
