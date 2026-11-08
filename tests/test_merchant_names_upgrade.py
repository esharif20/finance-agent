"""Moving a Ledger named by the pre-#310 normaliser (wallet prefixes kept, a
store's product folded into the store) onto the current one (M6 verifier):
decisions keyed by `merchant_id` stay on their rows and in force, including
when the new name collides with a Merchant that already exists."""

import re
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import Connection

from finance_agent import categorise
from finance_agent.ledger import Categorisation, Ledger
from test_review_queue import export, ledger_with

PHONE = ("Bills", "Phone & broadband")
GYM = ("Bills", "Energy")
_OLD_PROCESSOR = re.compile(r"^(SQ|SUMUP|ZTL|IZ) (?=\S)")

ROWS = [
    ("CRV*SYNTH CAFE 12", -350),  # was "CRV SYNTH CAFE", now "SYNTH CAFE"
    ("SYNTH CAFE 77", -400),  # already "SYNTH CAFE": the collision
    ("WL *SYNTH BAKERY*GOOG", -250),  # was "WL SYNTH BAKERY GOOG"
    ("GPAY SYNTH GYM", -3000),  # was "GPAY SYNTH GYM"
    ("GOOGLE *Colab", -999),  # was Google, now Google Colab
    ("GOOGLE *Colab", -999),
]


def _old_bare(words: list[str], processors: bool = True) -> list[str]:
    """Pre-#310: only a processor prefix went, and only for seed matching."""
    if not processors:
        return words
    return _OLD_PROCESSOR.sub("", " ".join(words)).split()


def _no_product(_squashed: str) -> str | None:
    return None  # pre-#310: "GOOGLE *Colab" was Google


@contextmanager
def old_names_ledger(
    mp: pytest.MonkeyPatch,
) -> Generator[tuple[Connection, Ledger]]:
    new_seeds = {"Google AI Pro", "Google Colab"}
    with ledger_with([]) as (conn, led), tempfile.TemporaryDirectory() as d:
        with mp.context() as old:
            old.setattr(categorise, "_bare", _old_bare)
            old.setattr(categorise, "_product", _no_product)
            old.setattr(
                categorise,
                "MERCHANTS",
                {k: v for k, v in categorise.MERCHANTS.items() if k not in new_seeds},
            )
            p = Path(d) / "export.csv"
            p.write_text(export(ROWS))
            led.import_files([p])
            categorise.run(conn, 1)
        yield conn, led


def by_description(led: Ledger) -> dict[str, Categorisation]:
    out: dict[str, Categorisation] = {}
    for t in led.transactions():
        assert t.categorisation is not None
        out[t.description] = t.categorisation
    return out


def test_decisions_on_pre_310_merchant_rows_survive_new_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with old_names_ledger(monkeypatch) as (conn, led):
        before = by_description(led)
        mid = {d: c.merchant_id or 0 for d, c in before.items()}
        assert before["CRV*SYNTH CAFE 12"].merchant == "CRV SYNTH CAFE"
        assert before["GOOGLE *Colab"].merchant == "Google"
        assert mid["CRV*SYNTH CAFE 12"] != mid["SYNTH CAFE 77"]
        # opposite decisions on the two rows that are about to collide
        led.correct_merchant(mid["CRV*SYNTH CAFE 12"], PHONE)
        led.correct_merchant(mid["SYNTH CAFE 77"], GYM)
        led.set_always_ask(mid["WL *SYNTH BAKERY*GOOG"], True)
        gym = next(
            t.id for t in led.transactions() if t.description == "GPAY SYNTH GYM"
        )
        led.correct_transaction(gym, GYM)
        led.add_subscription(mid["GOOGLE *Colab"], "subscription", "Streaming")
        entries = {(s.merchant_id, s.status) for s in led.subscriptions()}

        categorise.run(conn, 1)  # today's normaliser
        after = by_description(led)
        # no decided row is re-keyed: each keeps its id and its decision
        for d in ROWS[:3] + ROWS[4:5]:
            assert after[d[0]].merchant_id == mid[d[0]], d
        assert (
            after["CRV*SYNTH CAFE 12"].category,
            after["CRV*SYNTH CAFE 12"].source,
        ) == (
            PHONE,
            "correction",
        )
        assert (after["SYNTH CAFE 77"].category, after["SYNTH CAFE 77"].source) == (
            GYM,
            "correction",
        )
        assert after["WL *SYNTH BAKERY*GOOG"].source == "always_ask"
        assert (after["GPAY SYNTH GYM"].category, after["GPAY SYNTH GYM"].source) == (
            GYM,
            "correction",
        )
        # the registry entry is on the same row, none lost or orphaned
        assert {(s.merchant_id, s.status) for s in led.subscriptions()} >= entries
        known = {m.id for m in led.merchants()}
        assert all(s.merchant_id in known for s in led.subscriptions())

        categorise.run(conn, 1)  # idempotent
        assert by_description(led) == after
