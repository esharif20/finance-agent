"""The only code that reads or writes the macOS Keychain (via `keyring`, which
calls Security.framework directly, so secrets never appear on a command line).
Tests install an in-memory backend in conftest."""

import secrets

import keyring

DB_SERVICE = "finance-agent.db"
HMAC_SERVICE = "finance-agent.account-hmac"
ACCOUNT = "finance-agent"


def secret(service: str) -> bytes:
    """The 32-byte secret for `service`, generated on first use."""
    stored = keyring.get_password(service, ACCOUNT)
    if stored is None:
        stored = secrets.token_hex(32)
        keyring.set_password(service, ACCOUNT, stored)
    return bytes.fromhex(stored)
