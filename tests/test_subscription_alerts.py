# synthetic
"""Subscription alerts (#108): price rise, charged after cancel and a trial
converting within 2 days, each once per state, naming the Merchant and never
the amount. Synthetic data only."""

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from finance_agent import categorise
from finance_agent.categorise import subscription_alerts as alerts
from finance_agent.categorise.subscriptions import Charge, subscription_costs
from finance_agent.cli import main, watch
from finance_agent.ledger import Ledger, SubscriptionEntry
from finance_agent.notify import printed
from finance_agent.storage import open_db
from synthetic_lloyds import generate_histories, render_export
from test_subscription_costs import TODAY, entry
from test_subscription_flags import named

Export = Callable[[str], Path]
Found = set[tuple[int, str, date]]


def due(
    entries: list[SubscriptionEntry], charges: list[Charge], today: date = TODAY
) -> Found:
    s = subscription_costs(entries, charges, today)
    return {(a.merchant_id, a.code, a.state) for a in alerts.due(s, today)}


def test_a_price_rise_and_a_charge_after_cancel_are_due_on_their_evidence_date() -> (
    None
):
    rise = [Charge(1, date(2026, m, 1), 999) for m in (2, 3, 4)]
    rise += [Charge(1, date(2026, m, 1), 1199) for m in (5, 6)]
    after = [Charge(2, date(2026, m, 1), 599) for m in (1, 2, 3)]
    found = due(
        [entry(1), entry(2, status="user_cancelled", source="user")], rise + after
    )
    assert found == {
        (1, "price_rise", date(2026, 5, 1)),
        (2, "charged_after_cancel", date(2026, 2, 1)),
    }


def test_a_trial_is_due_only_within_two_days_of_converting() -> None:
    # SPOTIFY's trial converts 30 days after its small first charge
    def at(today: date, status: str = "active") -> Found:
        e = named(1, "SPOTIFY")
        if status != "active":
            from dataclasses import replace

            e = replace(e, status=status)
        return due([e], [Charge(1, date(2026, 6, 1), 99)], today)

    converts = date(2026, 7, 1)
    assert at(converts - timedelta(2)) == {(1, "trial_converting", converts)}
    assert at(converts) == {(1, "trial_converting", converts)}
    assert at(converts - timedelta(3)) == set()
    assert at(converts + timedelta(1)) == set()
    cancelled = at(converts - timedelta(1), "user_cancelled")
    assert {code for _, code, _ in cancelled} == {"charged_after_cancel"}
    converted = [Charge(1, date(2026, 6, 1), 99), Charge(1, converts, 1199)]
    assert due([named(1, "SPOTIFY")], converted, converts) == set()


def seed(engine: Engine, export: Export, user_id: int = 1) -> int:
    """A price rise a month ago: 10.99 three times, then 12.99 twice."""
    last = date.today() - timedelta(days=3)
    rows = "".join(
        f"{last - timedelta(days=30 * i):%d/%m/%Y},NETFLIX.COM,"
        f"{'-12.99' if i < 2 else '-10.99'}\n"
        for i in range(5)
    )
    with engine.begin() as conn:
        led = Ledger(conn, user_id)
        led.import_files([export(rows)])
        categorise.run(conn, user_id)
        (e,) = led.subscriptions()
        led.confirm_subscription(e.merchant_id)
        return e.merchant_id


def test_each_alert_fires_once_names_the_merchant_and_never_the_amount(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)
    sent: list[tuple[str, str]] = []
    assert alerts.run(engine, 1, lambda t, b: sent.append((t, b))) == 1
    ((title, body),) = sent
    assert title == "Finance Agent" and "NETFLIX" in body.upper()
    assert not any(x in body for x in ("12.99", "10.99", "1299", "1099", "£"))
    assert alerts.run(engine, 1, lambda t, b: sent.append((t, b))) == 0
    assert len(sent) == 1  # once per state


def test_one_users_alerts_never_suppress_or_reach_another(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export, 1)
    seed(engine, write_export, 2)
    mine: list[str] = []
    theirs: list[str] = []
    assert alerts.run(engine, 1, lambda _, b: mine.append(b)) == 1
    assert alerts.run(engine, 2, lambda _, b: theirs.append(b)) == 1
    assert alerts.run(engine, 3, lambda _, b: theirs.append(b)) == 0
    assert len(mine) == len(theirs) == 1


def test_a_failing_notifier_never_raises_and_the_alert_is_tried_again(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export)

    def broken(_: str, __: str) -> None:
        raise OSError("no osascript")

    assert alerts.run(engine, 1, broken) == 0  # import and sync carry on
    sent: list[str] = []
    assert alerts.run(engine, 1, lambda _, b: sent.append(b)) == 1


Calls = list[tuple[int, object]]


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    seen: Calls = []

    def record(_: Engine, user_id: int, notify: object, today: object = None) -> int:
        seen.append((user_id, notify))
        return 0

    monkeypatch.setattr(alerts, "run", record)
    return seen


def test_fa_import_checks_alerts_and_prints_them(
    calls: Calls, write_export: Export
) -> None:
    assert main(["import", str(write_export("01/01/2026,SYNTH SHOP,-1.00\n"))]) == 0
    assert calls == [(1, printed)]


def test_a_watched_folder_import_notifies_alerts(calls: Calls, tmp_path: Path) -> None:
    open_db().dispose()  # `fa import` creates the ledger; watch never does
    folder = tmp_path / "drop"
    folder.mkdir()
    (folder / "a.csv").write_text(
        render_export(generate_histories(3, 30)[0], 0, 9).text
    )
    notes: list[tuple[str, str]] = []
    assert watch.run(folder, lambda t, b: notes.append((t, b))) == 0
    ((user, notify),) = calls
    assert user == 1 and callable(notify)


def test_fa_forget_alerts_wipes_only_my_record_and_they_are_sent_again(
    engine: Engine, write_export: Export
) -> None:
    seed(engine, write_export, 1)
    seed(engine, write_export, 2)
    for user in (1, 2):
        assert alerts.run(engine, user, lambda _, __: None) == 1
    with engine.begin() as conn:
        assert alerts.forget(conn, 1) == 1
    assert alerts.run(engine, 2, lambda _, __: None) == 0  # theirs kept
    assert alerts.run(engine, 1, lambda _, __: None) == 1
    assert main(["forget-alerts"]) == 1  # never creates a ledger
    open_db().dispose()
    assert main(["forget-alerts"]) == 0
