import pytest

from finance_agent import ingest
from synthetic_lloyds import generate_histories, render_export


def test_account_numbers_are_masked_and_never_leave_ingest() -> None:
    h, _ = generate_histories(seed=2, days=10)
    parsed = ingest.parse(render_export(h, 0, 5).text.encode())

    assert parsed.bank == "Lloyds"
    for row in parsed.rows:
        assert row.masked_number == "••••0001"
        assert h.account_number not in repr(row)


def test_unknown_header_is_refused() -> None:
    with pytest.raises(ingest.UnknownFormat):
        ingest.parse(b"Date,Amount\n01/01/2026,1.00\n")
