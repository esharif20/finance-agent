"""The `fa` command. `fa import`, `fa summary` and `fa sync` call the Ledger
in process."""

import argparse
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from finance_agent import categorise
from finance_agent.cli import launcher
from finance_agent.identity import current_user
from finance_agent.ledger import Ledger, SyncStatus
from finance_agent.storage import open_db

LOCAL_USER = current_user()


def gbp(amount: Decimal) -> str:
    return f"{'-' if amount < 0 else ''}£{abs(amount):,.2f}"


def _print_status(st: SyncStatus) -> None:
    if st.last_sync_at:
        print(f"Last sync  {st.last_sync_at:%Y-%m-%d %H:%M} UTC")
    if st.failure_class and st.last_failure_at:
        print(
            f"Warning: last sync failed at {st.last_failure_at:%Y-%m-%d %H:%M} UTC "
            f"({st.failure_class}, {st.failures_in_a_row} in a row); "
            "these figures may be stale"
        )
    if st.consent_expires_on and st.consent_days_left is not None:
        left = st.consent_days_left
        if left < 0:
            print(
                f"Warning: bank consent expired {-left} days ago "
                f"({st.consent_expires_on})"
            )
        else:
            warn = "Warning: bank" if st.consent_warning else "Bank"
            print(f"{warn} consent expires {st.consent_expires_on} (in {left} days)")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fa")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("stop", help="stop the local server")
    imp = sub.add_parser(
        "import",
        help="import Bank CSV exports",
        description="Import Bank CSV exports. A file with any bad row is refused "
        "whole; a file no Importer recognises stops the whole batch.",
        epilog="exit status: 0 all imported (or already known); 1 nothing "
        "imported; 2 some files imported, others refused",
    )
    imp.add_argument("files", nargs="+", type=Path)
    summ = sub.add_parser("summary", help="money in, out and net for a month")
    summ.add_argument("month", metavar="YYYY-MM")
    w = sub.add_parser("watch", help="auto-import a watched folder (launchd)")
    wsub = w.add_subparsers(dest="watch_command", required=True)
    wi = wsub.add_parser("install", help="install the launchd agent")
    wi.add_argument("folder", type=Path)
    wsub.add_parser("remove", help="remove the launchd agent")
    wr = wsub.add_parser("run", help="import the folder now (launchd calls this)")
    wr.add_argument("folder", type=Path)
    con = sub.add_parser("connect", help="connect a SimpleFIN aggregator (setup token)")
    con.add_argument(
        "--renewed",
        action="store_true",
        help="record that the bank consent was renewed",
    )
    sub.add_parser("disconnect", help="remove the SimpleFIN access URL")
    sy = sub.add_parser("sync", help="fetch new Transactions from SimpleFIN")
    mode = sy.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="run daily (launchd)")
    mode.add_argument("--uninstall", action="store_true", help="stop the daily run")
    mode.add_argument("--notify", action="store_true", help="alert on trouble")
    args = parser.parse_args(argv)

    if args.command in ("connect", "disconnect", "sync"):
        from finance_agent.cli import bank
        from finance_agent.ingest.simplefin import SimpleFINError

        try:
            if args.command == "disconnect":
                return bank.disconnect()
            if args.command == "connect":
                return bank.connect(LOCAL_USER, args.renewed)
            if args.install or args.uninstall:
                from finance_agent.cli import schedule

                if args.install:
                    print(f"Installed {schedule.install()}")
                else:
                    schedule.remove()
                    print("Removed")
                return 0
            from finance_agent.cli import watch

            return bank.sync(LOCAL_USER, watch.osascript if args.notify else None)
        except (OSError, ValueError, SimpleFINError) as e:
            print(f"fa: {e}", file=sys.stderr)  # messages never carry the URL
            return 1

    if args.command is None:  # serving never creates the ledger; `fa import` does
        try:
            open_db(create=False).dispose()
        except (OSError, ValueError) as e:
            print(f"fa: {e}", file=sys.stderr)
            return 1
    if args.command in (None, "stop"):
        ln = launcher.default()
        try:
            if args.command is None:
                ln.start()
            elif not ln.stop():
                print("fa: the server is not running")
        except (OSError, launcher.ServerDidNotStart) as e:
            print(f"fa: {e}", file=sys.stderr)
            return 1
        return 0
    if args.command == "watch":
        from finance_agent.cli import watch

        if args.watch_command == "install":
            print(f"Installed {watch.install(args.folder)}")
        elif args.watch_command == "remove":
            watch.remove()
            print("Removed")
        else:
            return watch.run(args.folder)
        return 0

    try:
        engine = open_db()
    except (OSError, ValueError) as e:
        print(f"fa: {e}", file=sys.stderr)
        return 1
    try:
        with engine.begin() as conn:
            ledger = Ledger(conn, LOCAL_USER)
            if args.command == "import":
                r = ledger.import_files(args.files)
                categorise.run(conn, LOCAL_USER)
                print(
                    f"{r.added} added, {r.already_known} already known, "
                    f"{len(r.rejected)} rejected"
                )
                if r.ambiguous:
                    print(
                        f"{r.ambiguous} matched where twins made the "
                        "position uncertain; check them"
                    )
                if r.accounts:
                    masked = ", ".join(a.masked_number for a in r.accounts)
                    print(f"Accounts affected: {masked}")
                for rej in r.rejected:
                    print(f"  rejected {rej.file} line {rej.line}: {rej.reason}")
                if r.rejected:
                    return 2 if r.files_imported else 1
            else:
                try:
                    s = ledger.summarise_month(args.month)
                except ValueError:
                    print("month must be YYYY-MM", file=sys.stderr)
                    return 2
                print(f"{s.month}")
                print(f"Money in   {gbp(s.money_in)}")
                print(f"Money out  {gbp(s.money_out)}")
                print(f"Net        {gbp(s.net)}")
                if s.transfers_in or s.transfers_out:
                    print(f"Transfers  {gbp(s.transfers_out)} between your accounts")
                _print_status(ledger.sync_status())
                for g in s.gaps:
                    print(
                        f"Warning: Reconciliation Gap on {g.account.masked_number} "
                        f"between {g.from_date} and {g.to_date} of {gbp(g.amount)}; "
                        "these figures may be incomplete"
                    )
    except (OSError, ValueError) as e:
        print(f"fa: {e}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0
