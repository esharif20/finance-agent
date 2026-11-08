"""Readable names for raw Merchant text, for display only (#210).

Bank text like "OPENAI CHATGPT SUBSCR OPENAI CHATGPT SU" (SimpleFIN joins
payee and description) is shown as "ChatGPT (OpenAI)". Nothing stored changes:
descriptions and Merchant names feed dedup and identity (ADR 0014).

The name keeps the raw tokens in order, dropping a repeated run, a cut-off
last word, filler (SUB, LTD, PAYMENT...) and trailing reference codes, then
puts ALL-CAPS words in Title Case. A known service shows its catalogue name.
Pure, and idempotent: a shown name shows as itself.
"""

from finance_agent.categorise.subscriptions import (
    KNOWN_SERVICES,
    SERVICE_DISPLAY,
    known_service,
)

FILLER = frozenset(
    {"SUB", "SUBS", "SUBSCR", "SUBSCRIPTION", "SUBSCRIPTIONS", "LTD", "LIMITED",
     "PAYMENT", "PAYMENTS", "CARD"}
)  # fmt: skip
ACRONYMS = frozenset(
    {"UK", "JD", "HMRC", "BT", "EE", "TV", "DVLA", "NHS", "HSBC", "TSB", "RAC",
     "AA", "BP", "ATM", "DD", "EDF", "OVO", "ITV", "ITVX", "BBC", "AI", "HM",
     "TFL", "USA", "PLC", "MUBI", "O2"}
)  # fmt: skip
# long words bank text often cuts off: a strict prefix of one at the end is dropped
LONG_WORDS = ("UNDERWRITING", "INSURANCE", "SERVICES", "INTERNATIONAL",
              "MEMBERSHIP", "MANAGEMENT", "ENTERTAINMENT", "SUBSCRIPTION")  # fmt: skip


def _u(t: str) -> str:
    return t.upper()


def _same(a: str, b: str, last: bool) -> bool:
    """Token a repeats b; the last token of a run may be cut off."""
    return _u(a) == _u(b) or (last and len(a) >= 2 and _u(b).startswith(_u(a)))


def _collapse(ts: list[str]) -> list[str]:
    """Drop a tail that repeats an earlier run: A B C + A B -> A B C."""
    for i in range(1, len(ts)):
        tail = ts[i:]
        for j in range(i - len(tail) + 1):
            if all(_same(t, ts[j + k], k == len(tail) - 1) for k, t in enumerate(tail)):
                return ts[:i]
    return ts


def _cut_off(ts: list[str]) -> list[str]:
    last = _u(ts[-1])
    earlier = [_u(t) for t in ts[:-1]]
    if len(ts) > 1 and (
        (len(last) >= 2 and any(e != last and e.startswith(last) for e in earlier))
        or (
            len(last) >= 3 and any(w != last and w.startswith(last) for w in LONG_WORDS)
        )
    ):
        return ts[:-1]
    return ts


def _is_ref(t: str) -> bool:
    digits = sum(c.isdigit() for c in t)
    return digits >= 4 or (digits >= 2 and any(c.isalpha() for c in t))


def _clean(ts: list[str]) -> list[str]:
    ts = _cut_off(_collapse(ts))
    ts = [t for t in ts if _u(t) not in FILLER] or ts
    while len(ts) > 1 and _is_ref(ts[-1]):
        ts = ts[:-1]
    return ts


def _title(t: str) -> str:
    if not t.isascii() or t != t.upper() or not any(c.isalpha() for c in t):
        return t  # already mixed case, or no letters: left as written
    if t in ACRONYMS:
        return t
    low = t.lower()
    i = next(i for i, c in enumerate(low) if c.isalpha())
    return low[:i] + low[i].upper() + low[i + 1 :]


def display_name(raw: str) -> str:
    """The name to show for a Merchant's raw text; never empty for non-blank text."""
    if raw in CATALOGUE_NAMES or not raw.split():
        return raw
    known = known_service(raw)
    if known:
        return SERVICE_DISPLAY.get(known[0], known[0])
    return " ".join(_title(t) for t in cleaned(raw).split())


def cleaned(raw: str) -> str:
    """The raw tokens without repeats, cut-off words, filler or reference codes."""
    ts = raw.split()
    while ts and (nxt := _clean(ts)) != ts:  # each step only drops tokens: it ends
        ts = nxt
    return " ".join(ts)


CATALOGUE_NAMES = frozenset(SERVICE_DISPLAY.get(n, n) for n in KNOWN_SERVICES)
