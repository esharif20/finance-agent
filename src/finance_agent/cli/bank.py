"""`fa connect`, `fa disconnect` and `fa sync` (SimpleFIN, ADR 0013/0014).

The access URL lives only in the Keychain; it is read here and handed to the
ingest client, and never printed, logged or put in an error message."""

import getpass
import logging
import sys
from collections.abc import Callable
from datetime import datetime

from keyring.errors import KeyringError
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.categorise import auto, subscription_alerts
from finance_agent.cli import gbp
from finance_agent.cli.watch import TITLE, Notifier
from finance_agent.ingest import simplefin
from finance_agent.ledger import Ledger, SyncReport
from finance_agent.ledger.sync import SYNC_LOCK as SYNC_LOCK  # noqa: PLC0414
from finance_agent.ledger.sync import NotConnected, run_sync
from finance_agent.notify import printed
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


def connect(
    user_id: int, renewed: bool = False, link: bool = False, token: str | None = None
) -> int:
    """Connect once; `link` instead links synced Accounts to CSV Accounts
    imported since (ADR 0014). `token`: a setup token already read through a
    hidden prompt or stdin (`fa bank`, `fa setup`); never from argv."""
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
                token = token or getpass.getpass(
                    "Lunch Flow setup token (input hidden): "
                )
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
    if r.skipped_accounts:
        print(f"  {r.skipped_accounts} account skipped: unsupported currency")
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
        reports = run_sync(
            engine,
            user_id,
            _print,
            (lambda: _alert(engine, user_id, notify)) if notify else None,
        )
        if any(r.added for r in reports):  # same as `fa import`
            with engine.begin() as conn:
                categorise.run(conn, user_id)
            auto.refresh_quietly(engine, user_id)
        # every sync, so a trial converting soon is announced in time
        subscription_alerts.run(engine, user_id, notify or printed)
    except NotConnected as e:
        return _err(str(e))
    except Busy:
        print("fa: another sync is running; this one skipped")
    finally:
        engine.dispose()
    return 0


# --- `fa bank`: the guided Lunch Flow journey (#311) ------------------------------
# The one page we know is the pinned host's own (ADR 0013, spike #26): no deeper
# Lunch Flow URL is guessed, so each step names the page to find there. The site
# and its payments are never automated; they stay in the user's browser.
LUNCH_FLOW_URL = f"https://{simplefin.HOST}/"
INTRO = (
    "fa gets your transactions from Lunch Flow, which connects to your banks "
    "through UK open banking. It is read-only: it can never move money. Lunch "
    "Flow is a paid subscription, billed by Lunch Flow, not by us; see the "
    "current price on its sign-up page."
)
DESTINATION = (
    "In Lunch Flow, add a destination and choose SimpleFIN. Copy the setup token "
    "it shows."
)
Opener = Callable[[str], object]
Ask = Callable[[str], str]


def _yes(ask: Ask, prompt: str) -> bool:
    return ask(f"{prompt} [Y/n] ").strip().lower() in ("", "y", "yes")


def _open(open_url: Opener, ask: Ask, what: str) -> None:
    if _yes(ask, f"Open {LUNCH_FLOW_URL} ({what}) in your browser?"):
        open_url(LUNCH_FLOW_URL)


def found(access: str | None = None) -> dict[str, int]:
    """Bank name -> account count, from one read of the provider."""
    access = access or keychain.get_text(SERVICE)
    if access is None:
        return {}
    today = datetime.now(simplefin.LONDON).date()
    accounts = simplefin.parse(
        simplefin.fetch(access, today, today, simplefin.transport)
    ).accounts
    banks: dict[str, int] = {}
    for a in accounts:
        banks[a.org] = banks.get(a.org, 0) + 1
    return banks


def _summary(banks: dict[str, int], expected: int | None) -> bool:
    for org, n in sorted(banks.items()):
        print(f"  {org}: {n} account{'s' if n != 1 else ''}")
    if expected is not None and len(banks) < expected:
        print(
            f"{len(banks)} of the {expected} banks you expected arrived. Some banks "
            "take a few minutes to appear in Lunch Flow; sync again shortly."
        )
        return False
    return True


def guide(
    user_id: int,
    open_url: Opener,
    ask: Ask = input,
    token: str | None = None,
    read: Ask | None = None,
) -> int:
    """The three-step journey, then connect, first sync and a check that every
    bank the user connected arrived."""
    if keychain.get_text(SERVICE) is not None:
        print("Already connected. `fa bank add` adds a bank; `fa bank status` checks.")
        return 0
    print(f"1. {INTRO}")
    _open(open_url, ask, "sign up and subscribe")
    ask("   Press Enter when your Lunch Flow account is ready. ")
    print("2. In Lunch Flow, connect each bank you want. You can add more later.")
    raw = ask("   How many banks did you connect? ").strip()
    expected = int(raw) if raw.isdigit() and int(raw) > 0 else None
    print(f"3. {DESTINATION}")
    _open(open_url, ask, "add the SimpleFIN destination")
    if token is None:
        from finance_agent.cli.keys import read_secret

        token = (read or read_secret)("   Lunch Flow setup token (input hidden): ")
    rc = connect(user_id, token=token)
    if rc:
        return rc
    sync(user_id)
    while not _summary(found(), expected):
        if not _yes(ask, "Sync again now?"):
            break
        sync(user_id)
    return 0


def add(user_id: int, open_url: Opener, ask: Ask = input) -> int:
    if keychain.get_text(SERVICE) is None:
        return _err("not connected; run `fa bank` first")
    before = found()
    print(
        "Connect the new bank in Lunch Flow. No new token is needed: the SimpleFIN "
        "destination covers every bank you connect there."
    )
    _open(open_url, ask, "connect a bank")
    ask("Press Enter when the bank shows as connected in Lunch Flow. ")
    sync(user_id)
    after = found()
    new = {k: v for k, v in after.items() if v > before.get(k, 0)}
    if new:
        print("New:")
        _summary(new, None)
    else:
        print("No new accounts yet; banks can take a few minutes. Try `fa bank sync`.")
    return 0


def manage(open_url: Opener, ask: Ask = input) -> int:
    print(
        "Lunch Flow's account and billing pages are where you remove a bank, "
        "reconnect an expired one or cancel the subscription."
    )
    _open(open_url, ask, "account and billing")
    return 0


def status(user_id: int) -> int:
    """Connected or not, last sync, account count, and connections the provider
    says need attention (its messages are counted, never shown or kept)."""
    access = keychain.get_text(SERVICE)
    if access is None:
        print("Not connected. Run `fa bank` to set up Lunch Flow.")
        return 1
    engine = open_db(create=False)
    try:
        with engine.begin() as conn:
            st = Ledger(conn, user_id).sync_status()
    finally:
        engine.dispose()
    last = f"{st.last_sync_at:%Y-%m-%d %H:%M} UTC" if st.last_sync_at else "never"
    today = datetime.now(simplefin.LONDON).date()
    resp = simplefin.parse(simplefin.fetch(access, today, today, simplefin.transport))
    print(f"Connected to Lunch Flow · last sync {last} · {len(resp.accounts)} accounts")
    if resp.errors:
        print(
            f"Warning: Lunch Flow reports {resp.errors} connection problems; a bank "
            "may need re-authorising there (`fa bank manage`)."
        )
    if st.consent_warning:
        print("Warning: bank consent is near or past expiry; renew it in Lunch Flow.")
    return 0
