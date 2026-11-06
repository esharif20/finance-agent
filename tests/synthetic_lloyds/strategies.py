"""Hypothesis strategies over the generator."""

from hypothesis import strategies as st

from synthetic_lloyds.generator import (
    MAX_ROWS,
    Export,
    History,
    generate_histories,
    overlapping_windows,
    render_export,
)


@st.composite
def histories(draw: st.DrawFn) -> History:
    seed = draw(st.integers(0, 2**32 - 1))
    days = draw(st.integers(10, 300))
    return generate_histories(seed, days)[0]


@st.composite
def exports(draw: st.DrawFn) -> list[Export]:
    """Overlapping exports of one history, each at most MAX_ROWS rows."""
    h = draw(histories())
    n = len(h.txns)
    size = draw(st.integers(5, min(MAX_ROWS, n)))
    step = draw(st.integers(1, max(1, size - 1)))
    out: list[Export] = []
    for s, e in overlapping_windows(n, size, step):
        bad = draw(st.none() | st.integers(0, e - s))
        out.append(render_export(h, s, e, malformed_after=bad))
    return out
