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
    args = parser.parse_args(argv)

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
                for rej in r.rejected:
                    print(f"  rejected {rej.file} line {rej.line}: {rej.reason}")
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
