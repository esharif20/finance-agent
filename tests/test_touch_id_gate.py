"""#352: `fa` and `fa up` need Touch ID (or the Mac password) before any
Keychain read; a refusal reads nothing and exits 1."""

import sys

import pytest

from finance_agent import cli
from finance_agent.cli import touch_id

REAL_CONFIRM = touch_id.confirm  # conftest stubs the module attribute per test


@pytest.mark.parametrize("argv", [[], ["up"]])
def test_a_refusal_exits_before_the_database_is_opened(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(touch_id, "confirm", lambda reason=touch_id.REASON: False)

    def no_open(**_: object) -> object:
        raise AssertionError("the Keychain-backed database was opened without Touch ID")

    monkeypatch.setattr(cli, "open_db", no_open)
    assert cli.main(argv) == 1
    assert "Touch ID" in capsys.readouterr().err


def test_a_pass_goes_on_to_open_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(touch_id, "confirm", lambda reason=touch_id.REASON: True)
    opened: list[bool] = []

    def open_db(**_: object) -> object:
        opened.append(True)
        raise OSError("stop here")

    monkeypatch.setattr(cli, "open_db", open_db)
    assert cli.main(["up"]) == 1
    assert opened == [True]


def test_without_local_authentication_it_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "LocalAuthentication", None)  # import raises
    assert REAL_CONFIRM() is False
