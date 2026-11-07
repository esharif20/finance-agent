"""#27: the SimpleFIN Importer's parser and client, against synthetic responses
and a fake transport. No test here touches the network."""

import base64
import json
import secrets
import traceback
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from finance_agent.ingest import simplefin
from finance_agent.ingest.simplefin import SimpleFINError, UnreadableResponse

FIXTURES = Path(__file__).parent / "fixtures" / "synthetic"


def recorded(name: str) -> bytes:
    """A recorded synthetic response, minus the fixture guard's header line."""
    return (FIXTURES / name).read_bytes().split(b"\n", 1)[1]


def response(*accounts: dict[str, Any]) -> bytes:
    return json.dumps({"errors": [], "accounts": list(accounts)}).encode()


def account(*txns: dict[str, Any], **over: Any) -> dict[str, Any]:
    acc: dict[str, Any] = {
        "org": {"name": "Example Bank"},
        "id": "A1",
        "name": "Current",
        "currency": "GBP",
        "balance": "10.00",
        "balance-date": 1772366400,
        "transactions": list(txns),
    }
    return acc | over


def txn(pid: Any = "t1", amount: Any = "-4.50", **over: Any) -> dict[str, Any]:
    return {
        "id": pid,
        "posted": 1772323200,
        "amount": amount,
        "description": "X",
    } | over


def test_recorded_response_parses_with_opaque_ids_and_posted_rows_only() -> None:
    r = simplefin.parse(recorded("simplefin_accounts.json"))

    assert r.errors == 1
    cur, saver = r.accounts
    assert (cur.provider_id, saver.provider_id) == ("000123", "42")
    assert cur.org == "Example Bank" and cur.currency == "GBP"
    assert (cur.balance_minor, saver.balance_minor) == (105025, -1)
    assert cur.balance_ts == 1772366400
    # "0001" and "1" are distinct IDs; the identical repeat of "1" collapses.
    assert [t.provider_id for t in cur.rows] == ["0001", "1", "77"]
    first = cur.rows[0]
    assert (first.amount_minor, first.description) == (-450, "CAFE NERO")
    assert (first.posted_ts, first.posted_on) == (1772323200, date(2026, 3, 1))
    assert first.transacted_ts == 1772200000
    assert cur.rows[2].amount_minor == 200000
    assert cur.pending_minor == -1299
    assert not cur.conflicting_ids


def test_lunch_flow_shape_from_spike_26() -> None:
    current, card = simplefin.parse(recorded("simplefin_lunchflow.json")).accounts
    # 32-37 character IDs kept byte for byte; org.domain is null, org.name is used.
    assert [len(t.provider_id) for t in current.rows] == [32, 33]
    assert len(card.rows[0].provider_id) == 37
    assert current.org == "Synthetic Bank"
    # Empty description: payee is the text. Both present: payee then description,
    # once if they are the same.
    assert current.rows[0].description == "Synthetic Coffee"
    assert [t.description for t in card.rows] == [
        "Synthetic Grocer CARD PAYMENT REF 123",
        "Synthetic Grocer",
    ]
    # The pending row (posted == 0) is never dated or kept as a row.
    assert len(current.rows) == 2
    assert current.pending_minor == -999


def test_posted_date_is_the_europe_london_calendar_date() -> None:
    late_bst = int(datetime(2026, 7, 1, 23, 30, tzinfo=UTC).timestamp())
    (acc,) = simplefin.parse(response(account(txn(posted=late_bst)))).accounts
    assert acc.rows[0].posted_on == date(2026, 7, 2)


def test_same_id_with_different_fields_rejects_that_accounts_rows_only() -> None:
    bad = account(txn("t1"), txn("t1", amount="-5.00"))
    good = account(txn("t1"), id="A2")
    a, b = simplefin.parse(response(bad, good)).accounts
    assert a.conflicting_ids and a.rows == []
    assert not b.conflicting_ids and len(b.rows) == 1


@pytest.mark.parametrize(
    "bad",
    [
        b"not json",
        b"[]",
        response(account(txn(amount=4.5))),  # numbers would go through float
        response(account(txn(amount="1e3"))),
        response(account(txn(amount="4.505"))),  # below a penny
        response(account(txn(amount="٤.00"))),  # non-ASCII digit
        response(account(txn(pid=7))),  # IDs are opaque strings, never numbers
        response(account(txn(pid=""))),
        response(account(id=123)),
        response(account(currency="pounds")),
        response(account(balance="ten")),
        response(account(txn(posted="yesterday"))),
    ],
)
def test_malformed_responses_are_rejected_without_quoting_content(bad: bytes) -> None:
    with pytest.raises(UnreadableResponse) as e:
        simplefin.parse(bad)
    assert "4.5" not in str(e.value) and "pounds" not in str(e.value)


# --- client -----------------------------------------------------------------


def secret_url(path: str = "/simplefin") -> str:
    # Built at runtime: no credential-shaped literal in the repo (gitleaks).
    user, pw = secrets.token_hex(8), secrets.token_hex(16)
    return f"https://{user}:{pw}@{simplefin.HOST}{path}"


class FakeTransport:
    def __init__(self, *replies: tuple[int, bytes] | Exception) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def token_for(url: str) -> str:
    return base64.b64encode(url.encode()).decode()


def test_claim_posts_to_the_claim_url_and_returns_the_access_url() -> None:
    claim_url = f"https://{simplefin.HOST}/claim/{secrets.token_hex(8)}"
    access = secret_url()
    t = FakeTransport((200, access.encode()))

    assert simplefin.claim(token_for(claim_url), t) == access
    assert t.calls == [("POST", claim_url, {"Content-Length": "0"})]


@pytest.mark.parametrize(
    "claim_url",
    [
        "http://{host}/claim/x",  # not https
        "https://evil.example/claim/x",
        "https://{host}.evil.example/claim/x",
        "https://u:p@evil.example/claim/x",
    ],
)
def test_claim_refuses_hosts_off_the_allowlist_without_calling(claim_url: str) -> None:
    t = FakeTransport()
    with pytest.raises(SimpleFINError):
        simplefin.claim(token_for(claim_url.format(host=simplefin.HOST)), t)
    assert t.calls == []


def test_claim_refuses_an_access_url_off_the_allowlist() -> None:
    t = FakeTransport((200, b"https://u:p@evil.example/simplefin"))
    with pytest.raises(SimpleFINError):
        simplefin.claim(token_for(f"https://{simplefin.HOST}/claim/x"), t)


def test_fetch_sends_basic_auth_and_dates_without_credentials_in_the_url() -> None:
    access = secret_url()
    creds = access.split("//")[1].split("@")[0]
    t = FakeTransport((200, b"{}"))

    assert simplefin.fetch(access, date(2026, 1, 1), date(2026, 3, 1), t) == b"{}"
    ((method, url, headers),) = t.calls
    assert method == "GET" and creds not in url and "@" not in url
    assert url == (
        f"https://{simplefin.HOST}/simplefin/accounts"
        "?start-date=1767225600&end-date=1772323200&pending=1"
    )
    assert (
        headers["Authorization"] == "Basic " + base64.b64encode(creds.encode()).decode()
    )


def test_client_errors_never_carry_the_url_or_token() -> None:
    access = secret_url()
    claim_url = f"https://{simplefin.HOST}/claim/{secrets.token_hex(8)}"
    token = token_for(claim_url)
    failures: list[BaseException] = []
    for reply in (
        OSError(f"cannot reach {access} {claim_url}"),
        (403, access.encode()),
    ):
        for call in (
            lambda r=reply: simplefin.claim(token, FakeTransport(r)),
            lambda r=reply: simplefin.fetch(
                access, date(2026, 1, 1), date(2026, 1, 2), FakeTransport(r)
            ),
        ):
            with pytest.raises(SimpleFINError) as e:
                call()
            failures.append(e.value)
    with pytest.raises(SimpleFINError) as e:
        simplefin.claim(token + "!!", FakeTransport())
    failures.append(e.value)
    for err in failures:
        text = repr(err) + "".join(traceback.format_exception(err))
        for secret in (access, claim_url, token, access.split("@")[0]):
            assert secret not in text


def test_windows_stay_inside_the_provider_limit_and_the_request_budget() -> None:
    w = simplefin.windows(date(2026, 1, 1), date(2026, 1, 1))
    assert w == [(date(2026, 1, 1), date(2026, 1, 2))]
    long = simplefin.windows(date(2000, 1, 1), date(2026, 3, 1))
    assert len(long) == simplefin.MAX_REQUESTS
    assert long[-1][1] == date(2026, 3, 2)  # newest kept; older span shows as a Gap
    for start, end in long:
        assert 0 < (end - start).days <= simplefin.WINDOW_DAYS
    assert all(a[1] == b[0] for a, b in zip(long, long[1:], strict=False))


def test_the_real_transport_refuses_off_allowlist_hosts_before_connecting() -> None:
    with pytest.raises(SimpleFINError):
        simplefin.urllib_transport("GET", "https://evil.example/x", {}, None)
