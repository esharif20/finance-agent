"""Build the demo's six Persona ledgers in FA_DEMO_DIR/personas/ (plain SQLite),
from evals/synthetic_history.py's fictional users, through the real pipeline:
the Ledger imports each history as three CSV statements (current account,
credit card, savings), then categorise runs and a few Memories are confirmed.
Dates end today, so the latest month is the current one.
The Importer names its Bank, so the Bank is renamed to a fictional one after.
Run at deploy time; the files are never committed.

    FA_DEMO=1 FA_DEMO_DIR=... FA_DEMO_SECRET=... uv run python scripts/build_demo_db.py

CSV, not a SimpleFIN response: synced Accounts re-derive their HMAC ref on
every read, which would need a key, and the demo has no Keychain at all.
"""

import csv
import io
import os
import random
import secrets
import sys
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import keyring
import keyring.backend
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, update

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import synthetic_history as sh  # noqa: E402
from finance_agent import categorise, storage  # noqa: E402
from finance_agent.demo import guard  # noqa: E402
from finance_agent.demo.session import plain_engine  # noqa: E402
from finance_agent.identity import current_user  # noqa: E402
from finance_agent.ingest import lloyds  # noqa: E402
from finance_agent.ledger import Ledger, UnknownTarget  # noqa: E402
from finance_agent.ledger.proposals import (  # noqa: E402
    MemoryDraft,
    MemoryType,
    Proposals,
    Provenance,
)
from finance_agent.storage import schema  # noqa: E402

BANK = "Demo Bank"  # fictional: no real Bank name reaches the demo


class _Ephemeral(keyring.backend.KeyringBackend):
    """A throwaway in-memory key per service, so the import's keyed_ref works
    without the Keychain; the refs it hashes are synthetic."""

    priority = 1  # pyright: ignore[reportAssignmentType]

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.setdefault((service, username), secrets.token_hex(32))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.items.pop((service, username), None)


def _pounds(pence: int) -> str:
    return str(Decimal(pence).scaleb(-2).quantize(Decimal("0.01")))


# (date, description, signed pence, running balance); fictional descriptors
type Row = tuple[date, str, int, int]

FUEL = ("PETROLINE FUEL", "NORTHGATE FUEL STOP")
AI = (  # AI services, priced so some Personas run past a £100 monthly cap
    ("NEURALPAD AI", 1_800),
    ("PROSEMIND AI", 2_000),
    ("SKETCHLOOM AI", 1_200),
    ("VOICEWELL AI", 900),
    ("CODEWREN AI", 2_200),
    ("LUMEN RESEARCH AI", 2_400),
)
CARD_PAYMENT = "DEMO BANK CARD PAYMENT"
# (number, name): current, credit card, savings; synthetic, not real accounts
ACCOUNTS = (
    (90_001_000, "Everyday"),
    (90_002_000, "Credit card"),
    (90_003_000, "Savings"),
)
type Memory = tuple[MemoryType, str, tuple[str, ...]]
MEMORY_SETS: tuple[tuple[Memory, ...], ...] = (
    (
        (
            "goal",
            "Save £3,000 for a summer holiday by next June",
            ("holiday", "saving"),
        ),
        ("preference", "Keep takeaways to twice a week", ("takeaway",)),
        ("constraint", "Rent is fixed until the lease ends in spring", ("rent",)),
    ),
    (
        ("goal", "Build a six-month emergency fund", ("emergency-fund",)),
        ("preference", "Tell me bluntly when spending runs high", ("tone",)),
        ("constraint", "AI tools stay under £100 a month", ("ai", "subscriptions")),
    ),
    (
        ("goal", "Pay the credit card in full every month", ("credit-card",)),
        ("preference", "Fuel is a work cost; flag it if it doubles", ("fuel",)),
        ("goal", "Put £250 into savings each payday", ("saving",)),
    ),
)


def statement(rows: list[Row], number: str) -> str:
    """Rows as a statement in the CSV layout the Ledger already reads."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(lloyds.HEADER)
    for d, text, amount, balance in rows:
        debit, credit = (_pounds(-amount), "") if amount < 0 else ("", _pounds(amount))
        w.writerow(
            [
                d.strftime(lloyds.DATE_FORMAT),
                "DEB" if amount < 0 else "FPI",
                "00-00-00",
                number,
                text,
                debit,
                credit,
                _pounds(balance),
            ]
        )
    return out.getvalue()


def _running(rows: list[tuple[date, str, int]], opening: int) -> list[Row]:
    out: list[Row] = []
    for d, text, amount in rows:
        opening += amount
        out.append((d, text, amount, opening))
    return out


@contextmanager
def present(months: int, today: date) -> Generator[tuple[int, date]]:
    """(months to generate, first day kept) so the histories end in today's
    month. synthetic_history reads START when it dates rows and plants its
    seasons from a January START, so START moves to a January for the build
    (and back after) and the months before `months` ago are dropped."""
    years = max(0, -(-(months - today.month) // 12))
    total = years * 12 + today.month
    first = today.year * 12 + today.month - months
    held = sh.START
    sh.START = date(today.year - years, 1, 1)
    try:
        yield total, date(first // 12, first % 12 + 1, 1)
    finally:
        sh.START = held


def accounts(
    h: sh.History, today: date, since: date = date.min
) -> tuple[list[Row], list[Row], list[Row]]:
    """A current account, a credit card and savings for one history: the card
    carries fuel and AI services and is paid off in full each payday, the
    savings take each payday's sweep and earn interest. Rows from `since` to today."""
    rng = random.Random(f"demo-{h.id}")  # noqa: S311 - synthetic data, not security
    ai = [(m, p, rng.randint(1, 28)) for m, p in rng.sample(AI, rng.randint(3, 5))]
    spend: list[tuple[date, str, int]] = []
    for m in range(h.months):
        spend += [(sh.on_day(m, day), name, -price) for name, price, day in ai]
        spend += [
            (
                sh.on_day(m, rng.randint(1, 28)),
                rng.choice(FUEL),
                -rng.randint(3_500, 7_500),
            )
            for _ in range(rng.randint(2, 4))
        ]
    spend = sorted(r for r in spend if since <= r[0] <= today)

    current: list[tuple[date, str, int]] = []
    card: list[tuple[date, str, int]] = []
    savings: list[tuple[date, str, int]] = []
    paid_to, owed = date.min, 0
    txns = [t for t in h.txns if since <= t.date <= today]
    for t in txns:
        if t.kind == "salary":
            owed = -sum(a for d, _, a in spend if paid_to <= d < t.date)
            card += [r for r in spend if paid_to <= r[0] < t.date]
            paid_to = t.date
            current.append((t.date, t.merchant, t.amount))
        elif t.kind == "transfer":  # the sweep: it leaves room for the card bill
            current.append((t.date, t.merchant, t.amount + owed))
            if owed:
                current.append((t.date, CARD_PAYMENT, -owed))
                card.append((t.date, "PAYMENT RECEIVED - THANK YOU", owed))
            savings.append((t.date, "SWEEP FROM EVERYDAY", -(t.amount + owed)))
            owed = 0
        else:
            current.append((t.date, t.merchant, t.amount))
    card += [r for r in spend if r[0] >= paid_to]

    opening = txns[0].balance - txns[0].amount
    opened = saved = rng.randint(150_000, 600_000)
    with_interest: list[tuple[date, str, int]] = []
    month = None
    for r in savings:
        if month is not None and (r[0].year, r[0].month) != month:
            with_interest.append(
                (r[0].replace(day=1), "INTEREST EARNED", saved * 325 // 120_000)
            )
            saved += with_interest[-1][2]
        with_interest.append(r)
        saved += r[2]
        month = (r[0].year, r[0].month)
    return (
        _running(current, opening),
        _running(card, 0),
        _running(with_interest, opened),
    )


# Merchants left unanswered, so the Review screen holds a handful of items
FOR_REVIEW = {"BEAN THERE COFFEE", "MOONBEAM ROASTERS", "VOLTSPARK ELECTRONICS"}
EXTRA: dict[str, sh.Category] = {
    **{f: ("Transport", "Fuel") for f in FUEL},
    **{m: ("Subscriptions", "AI tools") for m, _ in AI},
}


def _settle(conn: Connection, h: sh.History) -> None:
    """Answer what a long-time user would have answered by now: each Merchant's
    Category and the subscriptions, all but FOR_REVIEW and one subscription."""
    led = Ledger(conn, current_user())
    truth = {t.merchant: t.category for t in h.txns} | EXTRA
    ids = {m.name: m.id for m in led.merchants()}
    for name, mid in ids.items():
        cat = truth.get(name, sh.NONE)
        if cat != sh.NONE and name not in FOR_REVIEW:
            led.correct_merchant(mid, cat)
    subs = sorted(
        name
        for name, (top, _) in truth.items()
        if top == "Subscriptions" or name in ("IRONWORKS GYM",)
    )
    for name in subs[1:]:
        if name in ids:
            try:
                led.confirm_subscription(ids[name])
            except UnknownTarget:  # not detected in this history: nothing to confirm
                pass


def _remember(conn: Connection, n: int, today: date) -> None:
    """A few confirmed Memories, as if the visitor had told the Assistant."""
    props = Proposals(conn, current_user())
    for i, (kind, text, tags) in enumerate(MEMORY_SETS[n % len(MEMORY_SETS)]):
        said = today - timedelta(days=9 * (i + 1))
        draft = MemoryDraft(
            type=kind,
            text=text,
            tags=tags,
            provenance=Provenance(turn_id=f"demo-{n}-{i}", quote=text, said_on=said),
        )
        props.confirm(props.propose_memory(draft).id)


def build(
    root: Path, histories: int = 6, months: int = sh.MONTHS, today: date | None = None
) -> list[Path]:
    today = today or date.today()
    keyring.set_keyring(_Ephemeral())
    out = root / "personas"
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(storage.__file__).parent / "migrations")
    )
    built: list[Path] = []
    with present(months, today) as (total, since):
        generated = [
            (h, accounts(h, today, since))
            for h in sh.generate(histories=histories, months=total)
        ]
    for n, (h, rows) in enumerate(generated):
        path = guard.inside(root, out / f"{n}.db")
        tmp = path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        files: list[Path] = []
        for (base, _), r in zip(ACCOUNTS, rows, strict=True):
            f = path.with_suffix(f".{base}.csv")
            f.write_text(statement(r, f"{base + h.id:08d}"))
            files.append(f)
        engine = plain_engine(tmp, create=True)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        with engine.begin() as conn:
            report = Ledger(conn, current_user()).import_files(files)
            if report.rejected or not report.added:
                raise SystemExit(f"build_demo_db: Persona {n} did not import cleanly")
            conn.execute(update(schema.bank).values(name=BANK))
            a = schema.account
            for base, name in ACCOUNTS:
                masked = lloyds.mask(f"{base + h.id:08d}")
                conn.execute(
                    update(a).where(a.c.masked_number == masked).values(name=name)
                )
            categorise.run(conn, current_user())
            _settle(conn, h)
            _remember(conn, n, today)
        engine.dispose()
        for f in files:
            f.unlink()
        os.replace(tmp, path)
        built.append(path)
    return built


if __name__ == "__main__":
    try:
        demo = guard.config()
    except guard.Refused as e:
        raise SystemExit(f"build_demo_db: {e}") from None
    for p in build(demo.dir):
        print(p)
