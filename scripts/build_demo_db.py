"""Build the demo's six Persona ledgers in FA_DEMO_DIR/personas/ (plain SQLite),
from evals/synthetic_history.py's fictional users, through the real pipeline:
the Ledger imports each history as a CSV statement, then categorise runs.
The Importer names its Bank, so the Bank is renamed to a fictional one after.
Run at deploy time; the files are never committed.

    FA_DEMO=1 FA_DEMO_DIR=... FA_DEMO_SECRET=... uv run python scripts/build_demo_db.py

CSV, not a SimpleFIN response: synced Accounts re-derive their HMAC ref on
every read, which would need a key, and the demo has no Keychain at all.
"""

import csv
import io
import os
import secrets
import sys
from decimal import Decimal
from pathlib import Path

import keyring
import keyring.backend
from alembic import command
from alembic.config import Config
from sqlalchemy import update

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals"))
import synthetic_history as sh  # noqa: E402
from finance_agent import categorise, storage  # noqa: E402
from finance_agent.demo import guard  # noqa: E402
from finance_agent.demo.session import plain_engine  # noqa: E402
from finance_agent.identity import current_user  # noqa: E402
from finance_agent.ingest import lloyds  # noqa: E402
from finance_agent.ledger import Ledger  # noqa: E402
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


def statement(h: sh.History) -> str:
    """One history as a statement in the CSV layout the Ledger already reads."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(lloyds.HEADER)
    number = f"{90_000_000 + h.id:08d}"  # synthetic, not a real account
    for t in h.txns:
        debit, credit = (
            (_pounds(-t.amount), "") if t.amount < 0 else ("", _pounds(t.amount))
        )
        w.writerow(
            [
                t.date.strftime(lloyds.DATE_FORMAT),
                "DEB" if t.amount < 0 else "FPI",
                "00-00-00",
                number,
                t.merchant,
                debit,
                credit,
                _pounds(t.balance),
            ]
        )
    return out.getvalue()


def build(root: Path, histories: int = 6, months: int = sh.MONTHS) -> list[Path]:
    keyring.set_keyring(_Ephemeral())
    out = root / "personas"
    out.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.set_main_option(
        "script_location", str(Path(storage.__file__).parent / "migrations")
    )
    built: list[Path] = []
    for n, h in enumerate(sh.generate(histories=histories, months=months)):
        path = guard.inside(root, out / f"{n}.db")
        tmp, csv_file = path.with_suffix(".tmp"), path.with_suffix(".csv")
        tmp.unlink(missing_ok=True)
        csv_file.write_text(statement(h))
        engine = plain_engine(tmp, create=True)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
        with engine.begin() as conn:
            report = Ledger(conn, current_user()).import_files([csv_file])
            if report.rejected or not report.added:
                raise SystemExit(f"build_demo_db: Persona {n} did not import cleanly")
            conn.execute(update(schema.bank).values(name=BANK))
            categorise.run(conn, current_user())
        engine.dispose()
        csv_file.unlink()
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
