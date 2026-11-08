"""The only code that reads or writes the macOS Keychain (via `keyring`, which
calls Security.framework directly, so secrets never appear on a command line).
Tests install an in-memory backend in conftest."""

import secrets

import keyring

DB_SERVICE = "finance-agent.db"
HMAC_SERVICE = "finance-agent.account-hmac"
# The SimpleFIN access URL: it carries the credentials (ADR 0013).
SIMPLEFIN_ACCESS_SERVICE = "finance-agent.simplefin-access"
ACCOUNT = "finance-agent"
# The Gemini API key for the privacy gateway (ADR 0016), made by the user with
# `security add-generic-password -s finance-agent.gemini -a api-key -w`.
GEMINI_SERVICE = "finance-agent.gemini"
GEMINI_ACCOUNT = "api-key"
# The #26 spike stored the real access URL under this account name; `fa connect`
# adopts it once, then it is deleted.
LEGACY_ACCOUNT = "lunchflow"


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


def get_text(service: str) -> str | None:
    return keyring.get_password(service, ACCOUNT)


def set_text(service: str, value: str) -> None:
    keyring.set_password(service, ACCOUNT, value)


def delete(service: str) -> bool:
    """Remove the item; False if there was none."""
    if keyring.get_password(service, ACCOUNT) is None:
        return False
    keyring.delete_password(service, ACCOUNT)
    return True


def legacy_text(service: str) -> str | None:
    return keyring.get_password(service, LEGACY_ACCOUNT)


def delete_legacy(service: str) -> None:
    keyring.delete_password(service, LEGACY_ACCOUNT)


def gemini_key() -> str | None:
    """The gateway's Gemini key, or None if the user has not stored one."""
    return keyring.get_password(GEMINI_SERVICE, GEMINI_ACCOUNT) or None
