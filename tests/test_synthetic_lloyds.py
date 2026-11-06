import csv
import io
from decimal import Decimal, InvalidOperation

from hypothesis import given
from hypothesis import strategies as st

from synthetic_lloyds import (
    HEADER,
    MAX_ROWS,
    Export,
    exports,
    generate_histories,
    overlapping_windows,
    render_export,
)


def parse(text: str) -> list[list[str]]:
    lines = text.splitlines()
    assert lines[0] == "# synthetic"
    return list(csv.reader(io.StringIO("\n".join(lines[1:]))))


def signed(row: list[str]) -> Decimal:
    return Decimal(row[6] or "0") - Decimal(row[5] or "0")


def test_same_seed_is_byte_identical() -> None:
    a, _ = generate_histories(7)
    b, _ = generate_histories(7)
    c, _ = generate_histories(8)
    assert render_export(a, 0, len(a.txns)) == render_export(b, 0, len(b.txns))
    assert render_export(a, 0, len(a.txns)) != render_export(c, 0, len(c.txns))


def test_layout_and_marker() -> None:
    h, _ = generate_histories(1)
    text = render_export(h, 0, 10).text
    assert text.startswith("# synthetic\n")
    rows = parse(text)
    assert rows[0] == HEADER
    assert HEADER == [
        "Transaction Date",
        "Transaction Type",
        "Sort Code",
        "Account Number",
        "Transaction Description",
        "Debit Amount",
        "Credit Amount",
        "Balance",
    ]
    assert len(rows) == 11


def test_export_row_cap() -> None:
    h, _ = generate_histories(2, days=400)
    assert len(h.txns) > MAX_ROWS
    for start, end in overlapping_windows(len(h.txns), MAX_ROWS, 100):
        assert len(parse(render_export(h, start, end).text)) - 1 <= MAX_ROWS


def test_oversized_export_rejected() -> None:
    h, _ = generate_histories(2, days=400)
    try:
        render_export(h, 0, MAX_ROWS + 1)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_windows_overlap_and_cover() -> None:
    w = overlapping_windows(400, 150, 100)
    assert w[0][0] == 0 and w[-1][1] == 400
    for (_, e1), (s2, _) in zip(w, w[1:], strict=False):
        assert s2 < e1


def test_balances_consistent() -> None:
    h, _ = generate_histories(3, days=120)
    rows = parse(render_export(h, 0, MAX_ROWS).text)[1:]
    for prev, cur in zip(rows, rows[1:], strict=False):
        assert Decimal(prev[7]) + signed(cur) == Decimal(cur[7])


def test_gap_breaks_consistency_once() -> None:
    h, _ = generate_histories(3, days=120)
    rows = parse(render_export(h, 0, 50, gap_at=20, gap_minor=1234).text)[1:]
    breaks = [
        i
        for i in range(1, len(rows))
        if Decimal(rows[i - 1][7]) + signed(rows[i]) != Decimal(rows[i][7])
    ]
    assert breaks == [20]


def test_malformed_row_line_number() -> None:
    h, _ = generate_histories(4)
    ex = render_export(h, 0, 10, malformed_after=5)
    assert isinstance(ex, Export) and ex.malformed_line is not None
    line = ex.text.splitlines()[ex.malformed_line - 1]
    row = next(csv.reader([line]))
    try:
        Decimal(row[5] or "0")
        Decimal(row[6] or "0")
        Decimal(row[7])
    except InvalidOperation:
        pass
    else:
        raise AssertionError("row parsed cleanly")
    assert render_export(h, 0, 10).malformed_line is None


def test_awkward_cases_present() -> None:
    a, b = generate_histories(5, days=90)
    assert any("REFUND" in t.description for t in a.txns)
    seen = {(t.date, t.description, t.amount_minor) for t in a.txns}
    assert len(seen) < len(a.txns)  # identical same-day purchases
    a_tfr = {(t.date, abs(t.amount_minor)) for t in a.txns if t.type == "TFR"}
    b_tfr = {(t.date, abs(t.amount_minor)) for t in b.txns if t.type == "TFR"}
    assert a_tfr and a_tfr == b_tfr
    assert a.account_number != b.account_number


def test_synthetic_identifiers_only() -> None:
    a, _ = generate_histories(6)
    assert a.sort_code == "00-00-00" and a.account_number.startswith("0000")


@given(st.data())
def test_strategy_exports_valid(data: st.DataObject) -> None:
    ex_list = data.draw(exports())
    assert ex_list
    for ex in ex_list:
        assert ex.text.startswith("# synthetic")
        assert len(parse(ex.text)) - 1 <= MAX_ROWS + 1
