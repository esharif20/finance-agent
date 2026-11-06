"""The only code that reads or writes the macOS Keychain (via `keyring`, which
calls Security.framework directly, so secrets never appear on a command line).
Tests install an in-memory backend in conftest."""

import secrets

import keyring

DB_SERVICE = "finance-agent.db"
HMAC_SERVICE = "finance-agent.account-hmac"
ACCOUNT = "finance-agent"


class MissingKey(LookupError):
    pass


def secret(service: str, *, create: bool = False) -> bytes:
    """The 32-byte secret for `service`. Generated only when `create` (a new
    database); otherwise a missing item fails closed rather than re-keying."""
    stored = keyring.get_password(service, ACCOUNT)
    if stored is None:
        if not create:
            raise MissingKey(f"no Keychain item for {service}")
        stored = secrets.token_hex(32)
        keyring.set_password(service, ACCOUNT, stored)
    return bytes.fromhex(stored)
