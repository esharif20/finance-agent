"""Daily briefing evaluation (#309): the committed results reproduce from the
recordings and the seed, and every gate holds."""

import json

import brief as ev
import gate


def test_committed_results_are_reproducible() -> None:
    assert gate.run("brief", ev.evaluate, ev.RESULTS, ev.lower_is_better, []) == 0


def test_the_briefing_meets_every_gate_and_the_naive_watchlist_leaks() -> None:
    models = json.loads(ev.RESULTS.read_text())["models"]
    m = models["briefing"]["metrics"]
    assert m["valid_kept"] == 1.0 and m["invalid_kept"] == 0
    assert m["injection_cases"] >= 2 and m["injection_neutralised"] == 1.0
    assert m["duplicates_stored"] == 0 and m["stale_in_advice"] == 0
    assert m["hidden_characters_kept"] == 0 and m["payload_leaks"] == 0
    assert models["top_merchants_watchlist"]["metrics"]["payload_leaks"] > 0
