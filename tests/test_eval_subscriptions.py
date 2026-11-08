"""The first-charge Subscriptions evaluation (#310) reproduces and gates CI:
recall rises over the frozen baseline, the false rate does not."""

import json

import subscriptions


def test_the_eval_passes_its_gate_and_reproduces() -> None:
    assert subscriptions.main([]) == 0
    committed = json.loads(subscriptions.RESULTS.read_text())
    assert subscriptions.evaluate() == committed["detector"]
    base, now = committed["baseline"], committed["detector"]
    assert now["recall"] > base["recall"]
    assert now["false_subscription_rate"] <= base["false_subscription_rate"]


def test_the_gate_catches_a_false_rate_rise() -> None:
    committed = json.loads(subscriptions.RESULTS.read_text())
    worse = {**committed["detector"], "false_subscription_rate": 0.5}
    assert "false-subscription rate above baseline" in subscriptions.regressions(
        worse, committed
    )
