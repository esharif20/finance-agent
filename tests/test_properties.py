import tempfile
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings

from finance_agent.ledger import Ledger
from finance_agent.storage import open_db
from synthetic_lloyds import MAX_ROWS, History, histories, render_export


@settings(max_examples=25, deadline=None)
@given(histories())
def test_monthly_totals_are_exact(h: History) -> None:
    rows = h.txns[:MAX_ROWS]
    with tempfile.TemporaryDirectory() as d:
        export = Path(d) / "export.csv"
        export.write_text(render_export(h, 0, len(rows)).text)
        engine = open_db(Path(d) / "ledger.db")
        with engine.begin() as conn:
            ledger = Ledger(conn, 1)
            ledger.import_files([export])
            for month in sorted({t.date.strftime("%Y-%m") for t in rows}):
                amounts = [
                    t.amount_minor for t in rows if t.date.strftime("%Y-%m") == month
                ]
                s = ledger.summarise_month(month)
                assert s.money_in == Decimal(sum(a for a in amounts if a > 0)) / 100
                assert s.money_out == Decimal(-sum(a for a in amounts if a < 0)) / 100
                assert s.net == s.money_in - s.money_out
                assert type(s.net) is Decimal
        engine.dispose()
