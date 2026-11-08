"""#215 F: the gate evaluation. Whatever the signals, the confident tier it
calibrates keeps >= 95% precision on them, and the committed run covers every
Merchant the rules leave. Synthetic labels only."""

import json
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

import categoriser
import categoriser_gate as g

LABELS = ["A › a", "A › b", "B › c"]
merchant = st.fixed_dictionaries(
    {
        "gold": st.sampled_from(LABELS),
        "rows": st.integers(1, 4),
        "knn": st.one_of(
            st.none(),
            st.tuples(st.integers(1, 1000), st.sampled_from(LABELS)).map(list),
        ),
        "llm": st.sampled_from([*LABELS, "unsure", None]),
    }
)


@settings(max_examples=300, deadline=None)
@given(st.dictionaries(st.text("xyz", min_size=1, max_size=4), merchant, max_size=40))
def test_the_confident_tier_keeps_95_percent_on_any_signals(
    signals: dict[str, dict[str, Any]],
) -> None:
    t = g.tiers(signals)
    picked = [
        (got[1] == s["gold"], s["rows"])
        for s in signals.values()
        if (got := t["confident"](s)) is not None
    ]
    n = sum(r for _, r in picked)
    if n:
        assert sum(ok * r for ok, r in picked) * 100 >= 95 * n


def test_the_committed_run_has_signals_for_every_residual_merchant() -> None:
    for path in sorted(categoriser.HERE.glob("results/categoriser_gate_*.json")):
        r = json.loads(path.read_text())
        assert r["missing_signals"] == []
        assert r["dataset_version"] == categoriser.DATASET_VERSION
        assert (
            r["review"]["items_per_sync_575"]
            <= r["review"]["cap"]
            + r["review"]["no_signal_merchants"]
            * 575
            // max(r["residual_merchants"], 1)
            + 1
        )
