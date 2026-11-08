"""Write web/src/landing/terminal/terminal.fixture.ts for the landing page's terminal
chapter: the `fa` banner's frames from cli/banner.py, pixel for pixel, and the real
output of `fa month` and `fa subs` (and `fa status`'s rows) for one synthetic
Persona, built through scripts/build_demo_db.py in a throwaway directory.

    uv run python scripts/export_terminal_fixture.py

No model is called and nothing leaves the machine. The Persona is fictional
(evals/synthetic_history.py); the figures are the CLI formatters' own output.
"""

import contextlib
import io
import json
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sqlalchemy import Connection, select  # noqa: E402

import build_demo_db  # noqa: E402
from finance_agent.cli import banner, gbp, status  # noqa: E402
from finance_agent.demo.session import plain_engine  # noqa: E402
from finance_agent.identity import current_user  # noqa: E402
from finance_agent.ledger import Ledger  # noqa: E402
from finance_agent.storage import schema  # noqa: E402

OUT = ROOT / "web" / "src" / "landing" / "terminal" / "terminal.fixture.ts"
ACCOUNTS_OUT = ROOT / "web" / "src" / "landing" / "accounts.fixture.ts"
DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def frames() -> tuple[list[str], list[str]]:
    """Every banner frame as two-character palette indices per pixel (.. is empty)."""
    palette: list[str] = []
    out: list[str] = []
    for i in range(banner.FRAMES):
        s = ""
        for row in banner.frame(i):
            for px in row:
                if px is None:
                    s += ".."
                    continue
                hexa = "#{:02x}{:02x}{:02x}".format(*px)
                if hexa not in palette:
                    palette.append(hexa)
                n = palette.index(hexa)
                s += DIGITS[n // 36] + DIGITS[n % 36]
        out.append(s)
    if len(palette) > len(DIGITS) ** 2:
        raise SystemExit("export_terminal_fixture: too many banner colours")
    return palette, out


def capture(fn: Callable[..., object], *args: object) -> list[str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().rstrip("\n").split("\n")


def outputs() -> tuple[dict[str, list[str]], dict[str, object]]:
    with tempfile.TemporaryDirectory() as tmp:
        # the last day of last month, so `fa month` shows a whole month
        today = date.today().replace(day=1) - timedelta(days=1)
        (db,) = build_demo_db.build(Path(tmp), histories=1, today=today)
        engine = plain_engine(db)
        user = current_user()
        now = datetime.combine(today, time(18), UTC)
        s = status.snapshot(engine, user, now.date())
        # `fa status` reads the Keychain, Ollama and backups too; on the page those are
        # what a fresh demo has: no cloud key, no model held, no backup yet
        rows = (
            ("Last sync", "never"),
            ("Accounts", str(s.accounts)),
            (f"{s.month} in", gbp(s.money_in)),
            (f"{s.month} out", gbp(s.money_out)),
            ("To review", str(s.review)),
            ("Subscriptions", f"{gbp(s.subs_monthly)} a month"),
            (
                "AI tools",
                f"{gbp(s.ai_monthly)} of £{status.AI_CAP} cap  "
                f"{status.bar(s.ai_monthly, status.AI_CAP, 12)}",
            ),
            ("Cloud", "off"),
            ("Model loaded", "none (idle)"),
            ("Last backup", "never"),
        )
        result = {
            "status": [f"  {k:<15}{v}" for k, v in rows],
            "month": capture(status.month, engine, user, None),
            "subs": capture(status.subs, engine, user, now.date()),
        }
        with engine.begin() as conn:
            accounts = combined(Ledger(conn, user), s.month)
            accounts["weekday"] = weekdays(conn, user, today)
        engine.dispose()
        return result, accounts


WEEKS = 13


def weekdays(conn: Connection, user: int, today: date) -> list[int]:
    """Average spending per weekday (Monday first) over the last 13 weeks, in pence,
    with transfers between the Persona's own Accounts left out."""
    t, tl = schema.transaction, schema.transfer_link
    legs = select(tl.c.from_transaction_id).union(select(tl.c.to_transaction_id))
    since = today - timedelta(weeks=WEEKS) + timedelta(days=1)
    rows = conn.execute(
        select(t.c.posted_on, t.c.amount_minor).where(
            t.c.user_id == user,
            t.c.posted_on >= since,
            t.c.posted_on <= today,
            t.c.amount_minor < 0,
            t.c.id.not_in(legs),
        )
    )
    total = [0] * 7
    for posted, amount in rows:
        total[posted.weekday()] -= amount
    return [round(x / WEEKS) for x in total]


def minor(d: Decimal | None) -> int:
    return int((d or Decimal(0)) * 100)


def combined(led: Ledger, month: str) -> dict[str, object]:
    """The Persona's Accounts and the month across them, as the Ledger adds it up:
    transfers between its own Accounts are left out of money in and out."""
    whole = led.summarise_month(month)
    rows: list[dict[str, object]] = []
    for a in led.accounts():
        m = led.summarise_month(month, a.id)
        rows.append(
            {
                "name": a.name,
                "kind": a.kind,
                "balance": minor(a.ledger_balance),
                "in": minor(m.money_in),
                "out": minor(m.money_out),
            }
        )
    return {
        "month": month,
        "bank": led.accounts()[0].bank if rows else "",
        "accounts": rows,
        "balance": sum(minor(a.ledger_balance) for a in led.accounts()),
        "in": minor(whole.money_in),
        "out": minor(whole.money_out),
        "moved": minor(whole.transfers_out),
    }


def write(path: Path, name: str, about: str, value: object) -> None:
    body = json.dumps(value, ensure_ascii=False, indent=1)
    path.write_text(
        "// synthetic: generated by scripts/export_terminal_fixture.py from "
        f"{about}.\n// Do not edit by hand.\n"
        f"export const {name} = {body} as const;\n"
    )
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> None:
    palette, fr = frames()
    out, accounts = outputs()
    write(
        ACCOUNTS_OUT,
        "ACCOUNTS",
        "one fictional demo Persona's Accounts, added up by the Ledger",
        accounts,
    )
    data = {
        "width": banner.WIDTH,
        "height": banner.HEIGHT,
        "frameMs": round(banner.FRAME_S * 1000),
        "tagline": banner.TAGLINE,
        "palette": palette,
        "frames": fr,
        "output": out,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    write(
        OUT,
        "TERMINAL",
        "cli/banner.py and the CLI formatters run on one fictional demo Persona",
        data,
    )


if __name__ == "__main__":
    main()
