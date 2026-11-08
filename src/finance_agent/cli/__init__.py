"""The `fa` command. `fa import`, `fa summary` and `fa sync` call the Ledger
in process."""

import argparse
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from finance_agent import categorise
from finance_agent.categorise import auto, embedding, subscription_alerts
from finance_agent.cli import launcher
from finance_agent.identity import current_user
from finance_agent.ingest.simplefin import SimpleFINError
from finance_agent.ledger import Ledger, SyncStatus
from finance_agent.notify import printed
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


UNEXPECTED = (
    "fa: unexpected {}; details are withheld because they may quote your data. "
    "Run the command again; if it keeps failing, report this error name."
)


def _brief(command: str) -> int:
    from datetime import date

    from finance_agent.cli import brief
    from finance_agent.gateway.cloud import CloudChatModel, CloudError
    from finance_agent.storage import default_db_path, keychain

    try:
        if command == "install":
            print(f"Installed {brief.install()}")
            return 0
        if command == "remove":
            brief.remove()
            print("Removed")
            return 0
        try:
            cloud = CloudChatModel(keychain.gemini_key() or "")
        except CloudError as e:
            print(f"fa: {e}; run `fa keys set gemini`", file=sys.stderr)
            return 1
        engine = open_db(create=False)
        try:
            return brief.refresh(
                LOCAL_USER, engine, default_db_path(), cloud, date.today()
            )
        finally:
            engine.dispose()
    except (OSError, ValueError) as e:
        print(f"fa: {e}", file=sys.stderr)  # messages never carry the key
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _main(argv)
    except Exception as e:  # last resort: str(e) or a traceback may quote data
        print(UNEXPECTED.format(type(e).__name__), file=sys.stderr)
        return 1


GROUPS = (
    ("Start", ("up", "setup", "demo", "stop")),
    ("See", ("status", "month", "subs", "summary")),
    ("Look after", ("doctor", "models", "keys", "bank", "uninstall")),
    (
        "Data",
        (
            "sync",
            "brief",
            "import",
            "watch",
            "connect",
            "disconnect",
            "backup",
            "restore",
            "forget-embeddings",
            "forget-alerts",
        ),
    ),
)


def _grouped_help(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> str:  # pyright: ignore[reportPrivateUsage]  # noqa: E501
    helps = {a.dest: a.help or "" for a in sub._choices_actions}  # pyright: ignore[reportPrivateUsage]
    lines = ["`fa` alone starts the app and opens it in your browser.", ""]
    for title, names in GROUPS:
        lines.append(f"{title}:")
        lines += [f"  {n:<19}{helps.get(n, '')}" for n in names]
        lines.append("")
    return "\n".join(lines)


def _front_door(args: argparse.Namespace) -> int | None:
    """The #311 commands; None when `args` is not one of them."""
    c = args.command
    if c == "demo":
        from finance_agent.cli import demo

        return (
            demo.list_personas()
            if args.list
            else demo.run(args.persona, open_browser=not args.no_open)
        )
    if c == "setup":
        from finance_agent.cli import banner, setup

        banner.show()
        ctx = setup.Ctx()
        if args.token_stdin:
            ctx.token = setup.read_token_stdin()
        return setup.run(ctx, restart=args.restart)
    if c == "uninstall":
        from finance_agent.cli import setup

        return setup.uninstall(args.purge)
    if c == "doctor":
        from finance_agent.cli import doctor

        return doctor.run()
    if c in ("models", "brain", "ai"):
        from finance_agent.cli import models

        if args.models_command == "install":
            return models.install(None if args.recommended else args.name)
        if args.models_command == "remove":
            return models.remove(args.name)
        return models.show()
    if c == "keys":
        from finance_agent.cli import keys

        if args.keys_command == "set":
            return keys.set_key(args.name)
        if args.keys_command == "remove":
            return keys.remove(args.name)
        if args.keys_command == "check":
            results = keys.check()
            for ok, what, fix in results:
                print(f"  {'✓' if ok else '✗'} {what}" + ("" if ok else f"  → {fix}"))
            return 0 if all(ok for ok, _, _ in results) else 1
        return keys.show()
    if c == "bank":
        from finance_agent.cli import bank, setup

        bc = args.bank_command
        if bc == "sync":
            return bank.sync(LOCAL_USER)
        if bc == "disconnect":
            return bank.disconnect()
        if bc == "status":
            return bank.status(LOCAL_USER)
        if bc == "add":
            return bank.add(LOCAL_USER, launcher._open_browser)  # pyright: ignore[reportPrivateUsage]
        if bc == "manage":
            return bank.manage(launcher._open_browser)  # pyright: ignore[reportPrivateUsage]
        token = setup.read_token_stdin() if args.token_stdin else None
        return bank.guide(LOCAL_USER, launcher._open_browser, input, token)  # pyright: ignore[reportPrivateUsage]
    if c in ("status", "month", "subs"):
        from finance_agent.cli import status

        engine = open_db(create=False)
        try:
            if c == "status":
                return status.status(engine, LOCAL_USER)
            if c == "month":
                try:
                    return status.month(engine, LOCAL_USER, args.month)
                except ValueError:
                    print("month must be YYYY-MM", file=sys.stderr)
                    return 2
            return status.subs(engine, LOCAL_USER)
        finally:
            engine.dispose()
    return None


def _up() -> int:
    """`fa` and `fa up`: the banner and a one-line status, then the UI."""
    from datetime import UTC, datetime

    from finance_agent.cli import banner, status

    try:
        engine = open_db(create=False)
    except (OSError, ValueError) as e:
        print(f"fa: {e}; new here? run `fa setup`", file=sys.stderr)
        return 1
    try:
        now = datetime.now(UTC)
        line = status.one_line(status.snapshot(engine, LOCAL_USER, now.date()), now)
    finally:
        engine.dispose()
    banner.show(line)
    try:
        launcher.default().start()
    except (OSError, launcher.ServerDidNotStart) as e:
        print(f"fa: {e}", file=sys.stderr)
        return 1
    return 0


def _add_front_door(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:  # pyright: ignore[reportPrivateUsage]  # noqa: E501
    sub.add_parser("up", help="start the app and open it (same as `fa`)")
    st = sub.add_parser("setup", help="guided setup; resumes where it stopped")
    st.add_argument(
        "--token-stdin", action="store_true", help="read the setup token from stdin"
    )
    st.add_argument("--restart", action="store_true", help="run every step again")
    de = sub.add_parser("demo", help="try it on synthetic data, no setup")
    de.add_argument("--persona", type=int, metavar="N", help="pick a persona")
    de.add_argument("--list", action="store_true", help="list the personas")
    de.add_argument("--no-open", action="store_true", help="don't open the browser")
    sub.add_parser("status", help="a terminal dashboard")
    mo = sub.add_parser("month", help="a month by Category, with bars")
    mo.add_argument("month", nargs="?", metavar="YYYY-MM")
    sub.add_parser("subs", help="subscriptions, monthly total and the AI cap")
    sub.add_parser("doctor", help="check keys, database, models, jobs and backups")
    md = sub.add_parser(
        "models", aliases=["brain", "ai"], help="local models: compare, install"
    )
    msub = md.add_subparsers(dest="models_command")
    mi = msub.add_parser("install", help="pull, check and select a model")
    which = mi.add_mutually_exclusive_group()
    which.add_argument("--recommended", action="store_true")
    which.add_argument("name", nargs="?", help="4b or 9b")
    mr = msub.add_parser("remove", help="delete a pulled model")
    mr.add_argument("name")
    ky = sub.add_parser("keys", help="Keychain items, by length only")
    ksub = ky.add_subparsers(dest="keys_command")
    ksub.add_parser("set", help="store a key (hidden prompt)").add_argument(
        "name", choices=["gemini"]
    )
    ksub.add_parser("remove", help="remove a key").add_argument("name")
    ksub.add_parser("check", help="check the keys")
    bk = sub.add_parser("bank", help="connect banks through Lunch Flow")
    bk.add_argument(
        "bank_command",
        nargs="?",
        choices=["status", "sync", "disconnect", "add", "manage"],
    )
    bk.add_argument(
        "--token-stdin", action="store_true", help="read the setup token from stdin"
    )
    un = sub.add_parser("uninstall", help="remove the app and its jobs; data stays")
    un.add_argument(
        "--purge", action="store_true", help="also delete data and keys (asks first)"
    )


def _main(argv: Sequence[str] | None) -> int:
    parser = argparse.ArgumentParser(
        prog="fa",
        usage="fa [<command>] [options]",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(
        dest="command", metavar="<command>", help=argparse.SUPPRESS
    )
    _add_front_door(sub)
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
    con = sub.add_parser("connect", help="connect Lunch Flow with a setup token")
    con.add_argument(
        "--renewed",
        action="store_true",
        help="record that the bank consent was renewed",
    )
    con.add_argument(
        "--link",
        action="store_true",
        help="link synced Accounts to CSV Accounts imported since connecting",
    )
    sub.add_parser("disconnect", help="remove the Lunch Flow access from the Keychain")
    sy = sub.add_parser("sync", help="fetch new Transactions from Lunch Flow")
    mode = sy.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="run daily (launchd)")
    mode.add_argument("--uninstall", action="store_true", help="stop the daily run")
    mode.add_argument("--notify", action="store_true", help="alert on trouble")
    br = sub.add_parser("brief", help="the daily news briefing on your topics")
    brsub = br.add_subparsers(dest="brief_command", required=True)
    brsub.add_parser("refresh", help="fetch today's items (launchd calls this)")
    brsub.add_parser("install", help="run it daily (launchd)")
    brsub.add_parser("remove", help="stop the daily run")
    bk = sub.add_parser(
        "backup",
        help="back up to the registered external drive",
        description="Back up the ledger, card art and config to the external "
        "drive registered with `fa backup setup`. Keeps 7 daily and 4 weekly copies.",
    )
    bk.add_argument("action", nargs="?", choices=["setup", "remove"])
    bk.add_argument(
        "--on-mount",
        action="store_true",
        help="back up only if the last one is over 24h old (launchd calls this)",
    )
    rs = sub.add_parser("restore", help="restore the ledger from a backup copy")
    rs.add_argument("copy", nargs="?", help="a copy's name or path (default: newest)")
    rs.add_argument(
        "--recovery-key",
        action="store_true",
        help="on a new Mac: read the recovery key from stdin (never pass it here)",
    )
    sub.add_parser(
        "forget-embeddings",
        help="delete every stored Merchant and search embedding (rebuilt when needed)",
    )
    sub.add_parser(
        "forget-alerts",
        help="delete the record of subscription alerts sent (#108)",
    )
    parser.epilog = _grouped_help(sub)
    args = parser.parse_args(argv)
    if args.command in (None, "up"):
        return _up()
    try:
        rc = _front_door(args)
    except (OSError, ValueError, LookupError) as e:
        print(f"fa: {e}", file=sys.stderr)  # messages never carry a secret
        return 1
    except SimpleFINError as e:
        print(f"fa: {e}", file=sys.stderr)  # messages never carry the URL
        return 1
    if rc is not None:
        return rc

    if args.command in ("forget-embeddings", "forget-alerts"):
        try:
            engine = open_db(create=False)
        except (OSError, ValueError) as e:
            print(f"fa: {e}", file=sys.stderr)
            return 1
        embeddings = args.command == "forget-embeddings"
        try:
            with engine.begin() as conn:
                wipe = embedding.forget if embeddings else subscription_alerts.forget
                n = wipe(conn, LOCAL_USER)
                if embeddings:  # and the search index's text vectors (#253)
                    from finance_agent.ledger import search_index

                    n += search_index.forget(conn, LOCAL_USER)
        finally:
            engine.dispose()
        print(
            f"Deleted {n} Merchant and search embeddings; the local model "
            "rebuilds them from your Transactions when it next runs"
            if embeddings
            else f"Deleted {n} sent subscription alerts; any still due is sent again"
        )
        return 0

    if args.command in ("backup", "restore"):
        from finance_agent.cli import backup

        try:
            if args.command == "restore":
                return backup.restore(args.copy, args.recovery_key)
            if args.action == "setup":
                return backup.setup()
            if args.action == "remove":
                return backup.remove()
            return backup.run(on_mount=args.on_mount)
        except (OSError, ValueError, LookupError) as e:
            print(f"fa: {e}", file=sys.stderr)  # messages never carry a key
            return 1

    if args.command in ("connect", "disconnect", "sync"):
        from finance_agent.cli import bank

        try:
            if args.command == "disconnect":
                return bank.disconnect()
            if args.command == "connect":
                return bank.connect(LOCAL_USER, args.renewed, args.link)
            if args.install or args.uninstall:
                from finance_agent.cli import schedule

                if args.install:
                    print(f"Installed {schedule.install()}")
                else:
                    schedule.remove()
                    print("Removed")
                return 0
            from finance_agent.cli import watch

            rc = bank.sync(LOCAL_USER, watch.osascript if args.notify else None)
            if rc == 0 and args.notify:  # the scheduled job: back up after a sync
                from finance_agent.cli import backup

                backup.run(notify=watch.osascript)
            return rc
        except (OSError, ValueError, SimpleFINError) as e:
            print(f"fa: {e}", file=sys.stderr)  # messages never carry the URL
            return 1

    if args.command == "stop":
        try:
            if not launcher.default().stop():
                print("fa: the server is not running")
        except OSError as e:
            print(f"fa: {e}", file=sys.stderr)
            return 1
        return 0
    if args.command == "brief":
        return _brief(args.brief_command)
    if args.command == "watch":
        from finance_agent.cli import watch

        try:
            if args.watch_command == "install":
                print(f"Installed {watch.install(args.folder)}")
            elif args.watch_command == "remove":
                watch.remove()
                print("Removed")
            else:
                return watch.run(args.folder)
        except (OSError, ValueError) as e:
            print(f"fa: {e}", file=sys.stderr)
            return 1
        return 0

    try:
        engine = open_db()
    except (OSError, ValueError) as e:
        print(f"fa: {e}", file=sys.stderr)
        return 1
    imported = False
    try:
        with engine.begin() as conn:
            ledger = Ledger(conn, LOCAL_USER)
            if args.command == "import":
                r = ledger.import_files(args.files)
                categorise.run(conn, LOCAL_USER)
                imported = True
                print(
                    f"{r.added} added, {r.already_known} already known, "
                    f"{len(r.rejected)} rejected"
                )
                if r.skipped:
                    print(f"{r.skipped} pending or reverted rows skipped")
                if r.ambiguous:
                    print(
                        f"{r.ambiguous} matched where twins made the "
                        "position uncertain; check them"
                    )
                if r.accounts:
                    masked = ", ".join(a.masked_number for a in r.accounts)
                    print(f"Accounts affected: {masked}")
                    if ledger.unlinked_synced_accounts():  # ADR 0014: say so
                        print(
                            "Synced Accounts are not linked to CSV Accounts yet, so "
                            "both are kept; if one is this Bank's, run "
                            "`fa connect --link`"
                        )
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
        if imported:  # after the commit: no transaction is held while Ollama works
            auto.refresh_quietly(engine, LOCAL_USER)
            subscription_alerts.run(engine, LOCAL_USER, printed)
        engine.dispose()
    return 0
