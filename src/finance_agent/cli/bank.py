"""`fa connect`, `fa disconnect` and `fa sync` (SimpleFIN, ADR 0013/0014).

The access URL lives only in the Keychain; it is read here and handed to the
ingest client, and never printed, logged or put in an error message."""

import getpass
import sys
from datetime import datetime

from sqlalchemy import Engine

from finance_agent.cli import gbp
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger, SyncReport
from finance_agent.storage import default_db_path, is_encrypted, keychain, open_db

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


def connect(user_id: int) -> int:
    engine = _encrypted_db()
    try:
        if keychain.get_text(SERVICE) is not None:
            return _err("already connected; run `fa disconnect` first")
        token = getpass.getpass("SimpleFIN setup token (input hidden): ")
        access = simplefin.claim(token, simplefin.transport)
        keychain.set_text(SERVICE, access)
        print("Connected. The access URL is stored in the Keychain only.")
        today = datetime.now(simplefin.LONDON).date()
        raw = simplefin.fetch(access, today, today, simplefin.transport)
        accounts = simplefin.parse(raw).accounts
        with engine.begin() as conn:
            ledger = Ledger(conn, user_id)
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
        print(f"  {r.provider_duplicates} rows look like provider duplicates; kept")
    if r.pending_explained:
        print(f"  {r.pending_explained} balance differences equal pending rows")
    if r.provider_errors:
        print(f"  the provider reported {r.provider_errors} messages")
    for g in r.gaps:
        print(
            f"Warning: Reconciliation Gap on {g.account.masked_number} at "
            f"{g.balance_at:%Y-%m-%d %H:%M} UTC: {gbp(g.amount)}"
        )


def sync(user_id: int) -> int:
    engine = _encrypted_db()
    try:
        access = keychain.get_text(SERVICE)
        if access is None:
            return _err("not connected; run `fa connect` first")
        today = datetime.now(simplefin.LONDON).date()
        with engine.begin() as conn:
            since = Ledger(conn, user_id).sync_since(today)
        for start, end in simplefin.windows(since, today):
            raw = simplefin.fetch(access, start, end, simplefin.transport)
            with engine.begin() as conn:  # each response commits on its own
                _print(Ledger(conn, user_id).apply_sync(raw))
        return 0
    finally:
        engine.dispose()
