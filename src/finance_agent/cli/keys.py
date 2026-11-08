"""`fa keys`: the app's Keychain items, shown by presence and length only.

Secrets are read through getpass only (never argv), refused when empty or
whitespace, and the stored length is read back and confirmed: on 8 Nov
`security add-generic-password -w` outside a real TTY stored an empty key.
The database keys are read-only here: losing one loses the ledger."""

import getpass
import sys
from collections.abc import Callable
from dataclasses import dataclass

from keyring.errors import KeyringError

from finance_agent.storage import keychain

Ask = Callable[[str], str]


@dataclass(frozen=True)
class Item:
    name: str
    label: str
    service: str
    account: str
    removable: bool


ITEMS = (
    Item("database", "database key", keychain.DB_SERVICE, keychain.ACCOUNT, False),
    Item("account-hmac", "account key", keychain.HMAC_SERVICE, keychain.ACCOUNT, False),
    Item(
        "bank",
        "Lunch Flow access",
        keychain.SIMPLEFIN_ACCESS_SERVICE,
        keychain.ACCOUNT,
        True,
    ),
    Item(
        "gemini",
        "Gemini API key",
        keychain.GEMINI_SERVICE,
        keychain.GEMINI_ACCOUNT,
        True,
    ),
)
BY_NAME = {i.name: i for i in ITEMS}
READ_ONLY = (
    "read-only here: losing it loses the ledger; `fa backup` and `fa restore` "
    "keep it safe"
)


class EmptySecret(ValueError):
    pass


def read_secret(prompt: str, hidden: Ask = getpass.getpass) -> str:
    """A secret from a hidden prompt; empty or whitespace is refused."""
    value = hidden(prompt).strip()
    if not value:
        raise EmptySecret("nothing was entered, so nothing was stored")
    return value


def store_secret(item: Item, value: str) -> int:
    """Store, read back and return the stored length; never prints the value."""
    keychain.set_item(item.service, value, item.account)
    back = keychain.item(item.service, item.account) or ""
    if len(back) != len(value):
        raise ValueError("the Keychain stored something else; nothing is trusted")
    return len(back)


def length(item: Item) -> int | None:
    try:
        v = keychain.item(item.service, item.account)
    except KeyringError:
        return None
    return len(v) if v else None


def show() -> int:
    for i in ITEMS:
        n = length(i)
        state = f"present, {n} characters" if n else "missing"
        note = f"  ({READ_ONLY})" if not i.removable else ""
        print(f"  {i.name:<13}{i.label:<20}{state}{note}")
    print("Values are never shown. Keychain keeps no change dates the app can read.")
    return 0


def set_key(name: str, hidden: Ask = getpass.getpass) -> int:
    item = BY_NAME.get(name)
    if item is None or not item.removable:
        print(f"fa: {name} cannot be set here", file=sys.stderr)
        return 2
    if item.name == "bank":
        print("fa: the Lunch Flow access comes from a setup token: run `fa bank`")
        return 2
    print(
        "Use a key from a paid-tier (billing-enabled) Google project: on the free "
        "tier Google may use prompts to improve its products, so the gateway "
        "refuses to start. Cloud spend is capped at £9 a month."
    )
    try:
        n = store_secret(item, read_secret(f"{item.label} (input hidden): ", hidden))
    except EmptySecret as e:
        print(f"fa: {e}", file=sys.stderr)
        return 1
    print(f"Stored the {item.label}: {n} characters.")
    return 0


def remove(name: str, ask: Ask = input) -> int:
    item = BY_NAME.get(name)
    if item is None:
        print(f"fa: no key called {name}", file=sys.stderr)
        return 2
    if not item.removable:
        print(f"fa: the {item.label} is {READ_ONLY}", file=sys.stderr)
        return 2
    if ask(f"Type '{item.name}' to remove the {item.label}: ").strip() != item.name:
        print("Nothing removed.")
        return 1
    gone = keychain.delete_item(item.service, item.account)
    print(f"Removed the {item.label}." if gone else "It was not there.")
    return 0


def check() -> list[tuple[bool, str, str]]:
    """(ok, line, fix) per key, for `fa keys check` and `fa doctor`."""
    out: list[tuple[bool, str, str]] = []
    for i in ITEMS:
        n = length(i)
        if i.name in ("database", "account-hmac"):
            out.append(
                (
                    n == 64,
                    f"{i.label} in Keychain",
                    "restore it: `fa restore --recovery-key`",
                )
            )
        elif i.name == "bank":
            out.append((bool(n), f"{i.label} in Keychain", "connect: `fa bank`"))
        else:
            out.append(
                (True, f"{i.label} {'stored' if n else 'not set (optional)'}", "")
            )
    return out
