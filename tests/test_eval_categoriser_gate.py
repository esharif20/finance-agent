"""#215 F: the gate evaluation. No model guess is confident (#287); the low
tier files a guess for every Merchant with a signal, its held-out tallies are
consistent, and the committed run covers every Merchant the rules leave.
Synthetic labels only."""

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


@settings(max_examples=200, deadline=None)
@given(st.dictionaries(st.text("xyz", min_size=1, max_size=4), merchant, max_size=40))
def test_the_low_tier_guesses_whenever_there_is_a_signal(
    signals: dict[str, dict[str, Any]],
) -> None:
    t = g.tiers(signals)
    for s in signals.values():
        got = t["low"](s)
        has = s["knn"] is not None or s["llm"] not in (None, "unsure")
        assert (got is not None) == has
        if got is not None:
            assert got[1] in LABELS


@settings(max_examples=15, deadline=None)
@given(st.dictionaries(st.text("xyz", min_size=1, max_size=4), merchant, max_size=15))
def test_held_out_tallies_count_every_guessed_row_once_per_repeat(
    signals: dict[str, dict[str, Any]],
) -> None:
    """#287 verifier: accuracy is measured on Merchants the tier was not fit
    on; each guessed row is scored once per repeat, never in-sample twice."""
    held = g.held_out(signals)
    guessed = sum(
        s["rows"]
        for s in signals.values()
        if s["knn"] is not None or s["llm"] not in (None, "unsure")
    )
    right, n = held.get("all", [0, 0])
    assert n == guessed * g.REPEATS and 0 <= right <= n
    assert sum(v[1] for k, v in held.items() if k != "all") == n


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
