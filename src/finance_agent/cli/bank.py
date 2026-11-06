"""`fa connect`, `fa disconnect` and `fa sync` (SimpleFIN, ADR 0013/0014).

The access URL lives only in the Keychain; it is read here and handed to the
ingest client, and never printed, logged or put in an error message."""

import getpass
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from keyring.errors import KeyringError
from sqlalchemy import Engine

from finance_agent.cli import gbp
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger, SyncReport
from finance_agent.storage import default_db_path, is_encrypted, keychain, open_db

log = logging.getLogger(__name__)
Notifier = Callable[[str, str], None]
TITLE = "Finance Agent"
LEGACY_HINT = (
    "fa: remove the old Keychain item yourself: "
    f"security delete-generic-password -s {keychain.SIMPLEFIN_ACCESS_SERVICE} "
    f"-a {keychain.LEGACY_ACCOUNT}"
)
SERVICE = keychain.SIMPLEFIN_ACCESS_SERVICE
REVOKE = (
    "To revoke access at the provider too, remove this connection in your "
    "Lunch Flow dashboard (and the bank consent in your banking app)."
)


def _err(message: str) -> int:
    print(f"fa: {message}", file=sys.stderr)
    return 1


def _encrypted_db() -> Engine:
    """Refuse to bring in real data unless the database is SQLCipher (ADR 0002)."""
    engine = open_db()  # raises UnencryptedDatabase on a plaintext file
    if not is_encrypted(default_db_path()):
        engine.dispose()
        raise ValueError("refusing to sync into an unencrypted database")
    return engine


def _adopt_spike_item() -> str | None:
    """Move the access URL the #26 spike stored under the legacy Keychain account
    to the app's account. Returns it, or None when there is nothing to adopt.
    A failed move keeps the legacy item (the setup token is already spent, so it
    is the only copy) and raises ValueError."""
    legacy = keychain.legacy_text(SERVICE)
    if legacy is None:
        return None
    try:
        keychain.set_text(SERVICE, legacy)
    except (OSError, KeyringError):
        raise ValueError(
            "could not move the old Keychain item; it is untouched, try again"
        ) from None
    try:
        keychain.delete_legacy(SERVICE)
    except (OSError, KeyringError):
        print(LEGACY_HINT, file=sys.stderr)
    return legacy


def connect(user_id: int, renewed: bool = False) -> int:
    engine = _encrypted_db()
    try:
        today = datetime.now(simplefin.LONDON).date()
        if renewed:  # the bank consent was renewed: just reset the 90-day clock
            with engine.begin() as conn:
                Ledger(conn, user_id).record_consent_renewed(today)
            print("Bank consent renewal recorded.")
            return 0
        if keychain.get_text(SERVICE) is not None:
            return _err("already connected; run `fa disconnect` first")
        access = _adopt_spike_item()
        if access is None:
            token = getpass.getpass("SimpleFIN setup token (input hidden): ")
            access = simplefin.claim(token, simplefin.transport)
            keychain.set_text(SERVICE, access)
        print("Connected. The access URL is stored in the Keychain only.")
        raw = simplefin.fetch(access, today, today, simplefin.transport)
        accounts = simplefin.parse(raw).accounts
        with engine.begin() as conn:
            ledger = Ledger(conn, user_id)
            ledger.record_consent_renewed(today)
            existing = {a.masked_number: a.id for a in ledger.accounts()}
            for acc in accounts:
                print(f"Synced Account: {acc.org} {acc.name} ({acc.currency})")
                if not existing:
                    continue
                choice = input(
                    "  Link it to a CSV Account? Enter its masked number "
                    f"({', '.join(existing)}) or leave blank: "
                ).strip()
                if choice:
                    if choice not in existing:
                        return _err("no Account with that masked number")
                    ledger.link_synced_account(acc.provider_id, existing.pop(choice))
                    print(f"  Linked to {choice}")
        return 0
    finally:
        engine.dispose()


def disconnect() -> int:
    removed = keychain.delete(SERVICE)
    print(
        "Disconnected: access URL removed from the Keychain."
        if removed
        else "Not connected."
    )
    print(REVOKE)
    return 0


def _print(r: SyncReport) -> None:
    print(
        f"{r.added} added, {r.matched} matched to CSV rows, "
        f"{r.already_known} already known"
    )
    if r.accounts:
        print(f"Accounts affected: {', '.join(a.masked_number for a in r.accounts)}")
    if r.changed_known:
        print(f"  {r.changed_known} known rows arrived changed; kept as first received")
    if r.rejected_accounts:
        print(
            f"  {r.rejected_accounts} Accounts refused: one ID sent with different rows"
        )
    if r.provider_duplicates:
        print(
            f"  {r.provider_duplicates} rows are Possible Duplicates; kept, not merged"
        )
    if r.pending_explained:
        print(f"  {r.pending_explained} Gaps equal the pending rows' total")
    if r.provider_errors:
        print(f"  the provider reported {r.provider_errors} messages")
    for g in r.gaps:
        print(
            f"Warning: Reconciliation Gap on {g.account.masked_number} at "
            f"{g.balance_at:%Y-%m-%d %H:%M} UTC: {gbp(g.amount)}"
        )


def _alert(engine: Engine, user_id: int, notify: Notifier) -> None:
    """Notify about a failure or a nearing consent expiry, once per state."""
    today = datetime.now(simplefin.LONDON).date()
    with engine.begin() as conn:
        ledger = Ledger(conn, user_id)
        st = ledger.sync_status(today)
        key: str | None = None
        body = ""
        renew = "renew it, then run `fa connect --renewed`"
        if st.failure_class:
            key = f"fail:{st.failure_class}"
            body = f"Sync failed ({st.failure_class}), {st.failures_in_a_row} in a row"
        elif st.consent_warning and st.consent_days_left is not None:
            key = f"consent:{st.consent_expires_on}"
            body = (
                f"Bank consent expired; {renew}"
                if st.consent_days_left < 0
                else f"Bank consent expires in {st.consent_days_left} days; {renew}"
            )
        if ledger.alert_is_new(key) and key is not None:
            notify(TITLE, body)


def sync(user_id: int, notify: Notifier | None = None) -> int:
    """Fetch and apply. `notify` is set by the scheduled job only."""
    engine = _encrypted_db()
    try:
        access = keychain.get_text(SERVICE)
        if access is None:
            return _err("not connected; run `fa connect` first")
        try:
            _fetch_and_apply(engine, user_id, access)
        except Exception as e:  # recorded by class name only, then re-raised
            with engine.begin() as conn:
                Ledger(conn, user_id).record_sync_failure(
                    type(e).__name__, datetime.now(UTC)
                )
            log.warning("sync_failed class=%s", type(e).__name__)
            if notify:
                _alert(engine, user_id, notify)
            raise
        with engine.begin() as conn:
            Ledger(conn, user_id).record_sync_success(datetime.now(UTC))
        if notify:
            _alert(engine, user_id, notify)
        return 0
    finally:
        engine.dispose()


def _fetch_and_apply(engine: Engine, user_id: int, access: str) -> None:
    today = datetime.now(simplefin.LONDON).date()
    with engine.begin() as conn:
        since = Ledger(conn, user_id).sync_since(today)
    for start, end in simplefin.windows(since, today):
        raw = simplefin.fetch(access, start, end, simplefin.transport)
        with engine.begin() as conn:  # each response commits on its own
            _print(Ledger(conn, user_id).apply_sync(raw))
