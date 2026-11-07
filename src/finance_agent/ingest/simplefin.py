"""SimpleFIN Importer: the only code in the app that reaches the network (ADR 0013).

Field mapping for Lunch Flow's SimpleFIN Bridge, checked against spike #26
(field names and counts only): HOST serves claim and data; amounts are JSON
strings; IDs are 32-37 character strings; pending rows have posted == 0; one
Account sends an empty description on every row while payee is always filled;
org.domain is null; one request with a 730-day start returned ~24 months.
Field names are read only in `_account`, `_row` and `_text`; change them and
the constants here, nothing else.

Secrets: the setup token, claim URL and access URL never appear in a log line,
exception message or traceback raised from here.
"""

import base64
import binascii
import json
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from finance_agent.ingest.types import NOT_HUNDREDTHS, NOT_HUNDREDTHS_REASON

HOST = "www.lunchflow.app"  # the one allowlisted aggregator host (ADR 0013)
# Spike: one 730-day request returned 684-727 days per Account. Other SimpleFIN
# bridges truncate to 90 days (firefly-iii#12128); lower this for them.
WINDOW_DAYS = 730
FIRST_SYNC_DAYS = 730
OVERLAP_DAYS = 7  # re-fetch so late-posting rows are caught
CONSENT_DAYS = 90  # GoCardless bank consent; the bank re-asks after this
CONSENT_WARN_DAYS = 14  # warn this far ahead of expiry
MAX_REQUESTS = 5  # per run; firefly-iii#12538 hit the bridge's daily quota
# Spike: balance == available-balance even with a pending row, so balance
# includes pending rows; the Ledger subtracts them before reconciling (ADR 0014).
TIMEOUT_S = 30
MAX_RESPONSE_BYTES = 32 * 1024 * 1024  # spike: ~24 months of 2 Accounts is far less
MAX_TEXT = 1024  # characters in any text field; longer refuses the response
MAX_MINOR = 10**15  # |amount| in minor units; keeps BigInteger and Decimal sane
MAX_TS = 4_102_444_800  # 2100-01-01; past datetime's range is not a timestamp
LONDON = ZoneInfo("Europe/London")

_AMOUNT = re.compile(r"-?\d+(\.\d+)?", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)

Transport = Callable[[str, str, dict[str, str], bytes | None], tuple[int, bytes]]


class SimpleFINError(Exception):
    """A connect or sync failure. Messages never carry URLs or tokens."""


class UnreadableResponse(ValueError):
    """The response breaks the protocol. Never quotes the response."""


@dataclass(frozen=True)
class SyncedRow:
    provider_id: str  # opaque: compared byte for byte, never parsed
    posted_ts: int  # unix seconds
    posted_on: date  # Europe/London calendar date of posted_ts (ADR 0014)
    transacted_ts: int | None  # stored, used for nothing yet
    amount_minor: int
    description: str  # raw `payee` then raw `description`, see `_text`


@dataclass(frozen=True)
class SyncedAccount:
    provider_id: str
    org: str
    name: str
    currency: str
    balance_minor: int
    balance_ts: int
    rows: list[SyncedRow]  # posted rows only; empty when conflicting_ids
    pending_minor: int  # sum of pending amounts, which never enter the Ledger
    conflicting_ids: bool  # one ID with differing rows: this sync's rows rejected


@dataclass(frozen=True)
class SyncResponse:
    accounts: list[SyncedAccount]
    errors: int  # count of provider messages; their text is never kept


def _obj(v: Any, what: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise UnreadableResponse(f"{what} is not an object")
    return cast(dict[str, Any], v)


def _list(v: Any, what: str) -> list[Any]:
    if not isinstance(v, list):
        raise UnreadableResponse(f"{what} is not a list")
    return cast(list[Any], v)


def _str(obj: dict[str, Any], key: str, *, required: bool = True) -> str:
    v = obj.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or len(v) > MAX_TEXT:
        raise UnreadableResponse(f"field {key} is not text")
    return v


def _ts(obj: dict[str, Any], key: str) -> int:
    v = obj.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MAX_TS:
        raise UnreadableResponse(f"field {key} is not a timestamp")
    return v


def _minor(obj: dict[str, Any], key: str) -> int:
    text = _str(obj, key)
    if len(text) > 32 or not _AMOUNT.fullmatch(text):
        raise UnreadableResponse(f"field {key} is not a decimal amount")
    # hundredths: an Account in a currency without them is refused (#156)
    minor = Decimal(text).scaleb(2)
    if minor != minor.to_integral_value():
        raise UnreadableResponse(f"field {key} has sub-minor-unit digits")
    if abs(minor) >= MAX_MINOR:
        raise UnreadableResponse(f"field {key} is out of range")
    return int(minor)


def _row(t: dict[str, Any]) -> tuple[bool, SyncedRow]:
    pid = _str(t, "id")
    if not pid or len(pid) > 256:  # synced_key.provider_id
        raise UnreadableResponse("transaction id is empty")
    posted = _ts(t, "posted")
    pending = t.get("pending") is True or posted == 0
    transacted = _ts(t, "transacted_at") if t.get("transacted_at") is not None else None
    return pending, SyncedRow(
        provider_id=pid,
        posted_ts=posted,
        posted_on=datetime.fromtimestamp(posted, LONDON).date(),
        transacted_ts=transacted,
        amount_minor=_minor(t, "amount"),
        description=_text(t),
    )


def _text(t: dict[str, Any]) -> str:
    """Spike: payee is filled on every row, description is empty on one whole
    Account. Payee first, then description when it adds something."""
    payee = _str(t, "payee", required=False).strip()
    desc = _str(t, "description", required=False).strip()
    return " ".join(dict.fromkeys(p for p in (payee, desc) if p))


def _account(a: dict[str, Any]) -> SyncedAccount:
    pid = _str(a, "id")
    currency = _str(a, "currency")
    if not pid or len(pid) > 256 or not _CURRENCY.fullmatch(currency):
        raise UnreadableResponse("account id or currency is malformed")
    if currency in NOT_HUNDREDTHS:  # amounts are stored in hundredths (#156)
        raise UnreadableResponse(f"account {NOT_HUNDREDTHS_REASON}")
    org = _obj(a.get("org", {}), "org")
    # spike: org.domain is always null; name, then id, identifies the Bank
    org_name = _str(org, "name", required=False) or _str(org, "id", required=False)
    txns = _list(a.get("transactions", []), "transactions")
    posted: dict[str, SyncedRow] = {}
    # one ID is one row for pending rows too, so a repeat cannot cancel a Gap
    pending: dict[str, SyncedRow] = {}
    conflict = False
    for t in txns:
        is_pending, row = _row(_obj(t, "transaction"))
        seen = pending if is_pending else posted
        if row.provider_id not in seen:
            seen[row.provider_id] = row
        elif seen[row.provider_id] != row:
            conflict = True
    pending_minor = sum(r.amount_minor for r in pending.values())
    return SyncedAccount(
        provider_id=pid,
        org=org_name,
        name=_str(a, "name", required=False),
        currency=currency,
        balance_minor=_minor(a, "balance"),
        balance_ts=_ts(a, "balance-date"),
        rows=[] if conflict else list(posted.values()),
        pending_minor=pending_minor,
        conflicting_ids=conflict,
    )


_BAD = object()


def parse(data: bytes) -> SyncResponse:
    # Decimal for any JSON number so no float is ever created; amounts must be text.
    # The raise sits outside `except`: the decode error quotes the response.
    try:
        doc = json.loads(data, parse_float=Decimal)
    except (ValueError, RecursionError):
        doc = _BAD
    if doc is _BAD:
        raise UnreadableResponse("response is not JSON")
    top = _obj(doc, "response")
    accounts = [
        _account(_obj(a, "account")) for a in _list(top.get("accounts"), "accounts")
    ]
    errors = top.get("errors")
    return SyncResponse(accounts, len(_list(errors, "errors")) if errors else 0)


# --- client -----------------------------------------------------------------


def _check(url: str, *, credentials: bool) -> tuple[str, str]:
    """Refuse anything but https on HOST; returns (url without userinfo, userinfo)."""
    # Raises sit outside `except`: urllib's errors quote the URL.
    try:
        u = urlsplit(url)
        parts = (u, u.hostname, u.port, bool(u.username and u.password))
    except ValueError:
        parts = None
    if parts is None:
        raise SimpleFINError("the SimpleFIN URL is malformed")
    u, host, port, has_creds = parts
    if u.scheme != "https" or host != HOST or port not in (None, 443):
        raise SimpleFINError(f"only https://{HOST} is allowlisted (ADR 0013)")
    if has_creds != credentials or u.fragment:
        raise SimpleFINError("the SimpleFIN URL is malformed")
    userinfo = u.netloc.rpartition("@")[0]
    return u._replace(netloc=u.netloc.rpartition("@")[2]).geturl(), userinfo


def _call(
    transport: Transport, method: str, url: str, headers: dict[str, str]
) -> bytes:
    try:
        reply = transport(method, url, headers, b"" if method == "POST" else None)
    except Exception:  # HTTP client errors embed the URL: raise outside, unchained
        reply = None
    if reply is None:
        raise SimpleFINError("could not reach the SimpleFIN provider")
    status, body = reply
    if status != 200:
        raise SimpleFINError(f"the SimpleFIN provider answered HTTP {status}")
    if len(body) > MAX_RESPONSE_BYTES:
        raise SimpleFINError("the SimpleFIN response is too large")
    return body


def claim(setup_token: str, transport: Transport) -> str:
    """Exchange a one-time setup token for the access URL (holds credentials)."""
    # Raises sit outside `except`: decode errors keep the decoded bytes.
    try:
        claim_url = base64.b64decode(setup_token.strip(), validate=True).decode("ascii")
    except (binascii.Error, ValueError):
        claim_url = None
    if claim_url is None:
        raise SimpleFINError("that is not a SimpleFIN setup token")
    _check(claim_url, credentials=False)
    body = _call(transport, "POST", claim_url, {"Content-Length": "0"})
    try:
        access = body.decode("ascii").strip()
    except UnicodeDecodeError:
        access = None
    if access is None:
        raise SimpleFINError("the provider returned no access URL")
    _check(access, credentials=True)
    return access


def _midnight(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())


def fetch(access_url: str, start: date, end: date, transport: Transport) -> bytes:
    """One `/accounts` request for every Account, rows posted in [start, end)."""
    base, userinfo = _check(access_url, credentials=True)
    url = (
        f"{base.rstrip('/')}/accounts?start-date={_midnight(start)}"
        f"&end-date={_midnight(end)}&pending=1"
    )
    auth = base64.b64encode(userinfo.encode()).decode()
    return _call(transport, "GET", url, {"Authorization": f"Basic {auth}"})


def windows(since: date, until: date) -> list[tuple[date, date]]:
    """[start, end) windows covering since..until inclusive, oldest first, each at
    most WINDOW_DAYS. Beyond MAX_REQUESTS the oldest are dropped; the missing
    span then shows as a Reconciliation Gap rather than an over-quota run."""
    out: list[tuple[date, date]] = []
    end = until + timedelta(days=1)
    while end > since and len(out) < MAX_REQUESTS:
        start = max(since, end - timedelta(days=WINDOW_DAYS))
        out.append((start, end))
        end = start
    return out[::-1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None  # a redirect could leave the allowlisted host


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None
) -> tuple[int, bytes]:
    """The real transport: allowlisted host only, no proxies, no redirects."""
    _check(url, credentials=False)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        NoRedirect,
    )
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    try:
        with opener.open(req, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        return e.code, b""


transport: Transport = urllib_transport  # tests replace this with a fake
