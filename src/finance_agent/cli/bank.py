"""`fa connect`, `fa disconnect` and `fa sync` (SimpleFIN, ADR 0013/0014).

The access URL lives only in the Keychain; it is read here and handed to the
ingest client, and never printed, logged or put in an error message."""

import getpass
import logging
import sys
from datetime import datetime

from keyring.errors import KeyringError
from sqlalchemy import Engine

from finance_agent.cli import gbp
from finance_agent.cli.watch import TITLE, Notifier
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger, SyncReport
from finance_agent.ledger.sync import SYNC_LOCK as SYNC_LOCK  # noqa: PLC0414
from finance_agent.ledger.sync import NotConnected, run_sync
from finance_agent.storage import default_db_path, is_encrypted, keychain, open_db
from finance_agent.storage.lock import Busy

log = logging.getLogger(__name__)
LEGACY_HINT = (
    "fa: remove the old Keychain item yourself: "
    f"security delete-generic-password -s {keychain.SIMPLEFIN_ACCESS_SERVICE} "
    f"-a {keychain.LEGACY_ACCOUNT}"
)
SERVICE = keychain.SIMPLEFIN_ACCESS_SERVICE
KEYCHAIN_LOST = (
    "could not save the access URL in the Keychain, so it is lost. The setup "
    "token is now spent: remove this connection in your Lunch Flow dashboard, "
    "get a new setup token there and run `fa connect` again"
)
RELINK = "nothing was saved; run `fa connect --link` to try again"
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


def _store(access: str) -> None:
    """Save the freshly claimed access URL, retrying once: it is the only copy."""
    try:
        keychain.set_text(SERVICE, access)
    except (OSError, KeyringError):
        try:
            keychain.set_text(SERVICE, access)
        except (OSError, KeyringError):
            raise ValueError(KEYCHAIN_LOST) from None


def _link(ledger: Ledger, accounts: list[simplefin.SyncedAccount]) -> None:
    """Ask which CSV Account each synced Account is. A bad answer raises
    ValueError, so the caller's transaction rolls back whole."""
    csv = ledger.unlinked_csv_accounts()
    for acc in accounts:
        print(f"Synced Account: {acc.org} {acc.name} ({acc.currency})")
        if not csv or ledger.synced_account_linked(acc.provider_id):
            continue
        choice = input(
            "  Link it to a CSV Account? Enter its masked number "
            f"({', '.join(csv)}) or leave blank: "
        ).strip()
        if choice:
            if choice not in csv:
                raise ValueError(f"no Account with that masked number; {RELINK}")
            ledger.link_synced_account(acc.provider_id, csv.pop(choice))
            print(f"  Linked to {choice}")


def connect(user_id: int, renewed: bool = False, link: bool = False) -> int:
    """Connect once; `link` instead links synced Accounts to CSV Accounts
    imported since (ADR 0014)."""
    engine = _encrypted_db()
    try:
        today = datetime.now(simplefin.LONDON).date()
        if renewed:  # the bank consent was renewed: just reset the 90-day clock
            with engine.begin() as conn:
                Ledger(conn, user_id).record_consent_renewed(today)
            print("Bank consent renewal recorded.")
            return 0
        access = keychain.get_text(SERVICE)
        if link:
            if access is None:
                return _err("not connected; run `fa connect` first")
        else:
            if access is not None:
                return _err("already connected; run `fa disconnect` first")
            access = _adopt_spike_item()
            if access is None:
                token = getpass.getpass("SimpleFIN setup token (input hidden): ")
                access = simplefin.claim(token, simplefin.transport)
                _store(access)
            print("Connected. The access URL is stored in the Keychain only.")
        raw = simplefin.fetch(access, today, today, simplefin.transport)
        accounts = simplefin.parse(raw).accounts
        with engine.begin() as conn:  # one transaction: an error commits nothing
            ledger = Ledger(conn, user_id)
            # a connect whose linking failed left no consent date: set it now
            if not link or ledger.sync_status(today).consent_renewed_on is None:
                ledger.record_consent_renewed(today)
            _link(ledger, accounts)
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
        # always called: key None (all well) clears the memory, so a later
        # recurrence of the same problem is announced again
        new = ledger.alert_is_new(key)
        if key is not None and new:
            notify(TITLE, body)


def sync(user_id: int, notify: Notifier | None = None) -> int:
    engine = _encrypted_db()
    try:
        run_sync(
            engine,
            user_id,
            _print,
            (lambda: _alert(engine, user_id, notify)) if notify else None,
        )
    except NotConnected as e:
        return _err(str(e))
    except Busy:
        print("fa: another sync is running; this one skipped")
    finally:
        engine.dispose()
    return 0
