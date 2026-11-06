"""The `fa` command. `fa import` and `fa summary` call the Ledger in process."""

import argparse
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from finance_agent.ledger import Ledger
from finance_agent.storage import open_db

LOCAL_USER = 1  # single local user until the demo milestone (ADR 0003)


def gbp(amount: Decimal) -> str:
    return f"{'-' if amount < 0 else ''}£{abs(amount):,.2f}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fa")
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import", help="import Bank CSV exports")
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
    args = parser.parse_args(argv)

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
                print(
                    f"{r.added} added, {r.already_known} already known, "
                    f"{len(r.rejected)} rejected"
                )
                if r.accounts:
                    masked = ", ".join(a.masked_number for a in r.accounts)
                    print(f"Accounts affected: {masked}")
                for rej in r.rejected:
                    print(f"  rejected {rej.file} line {rej.line}: {rej.reason}")
                if r.rejected:
                    return 1
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
    except (OSError, ValueError) as e:
        print(f"fa: {e}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0
