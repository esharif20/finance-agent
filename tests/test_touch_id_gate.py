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


class _FakeContext:
    """Stands in for LAContext; `reply_with` is what the reply block gets,
    `raises` makes evaluatePolicy throw, `silent` never calls back."""

    reply_with: object = True
    raises = False
    silent = False

    @classmethod
    def new(cls) -> "_FakeContext":
        return cls()

    def canEvaluatePolicy_error_(  # noqa: N802
        self, _policy: object, _err: object
    ) -> tuple[bool, None]:
        return True, None

    def evaluatePolicy_localizedReason_reply_(  # noqa: N802
        self, _policy: object, _reason: str, reply: object
    ) -> None:
        if self.raises:
            raise RuntimeError("objc.error")
        if not self.silent:
            reply(self.reply_with, None)  # type: ignore[operator]

    def invalidate(self) -> None:
        pass


def _fake_la(monkeypatch: pytest.MonkeyPatch, **attrs: object) -> None:
    import types

    ctx = type("Ctx", (_FakeContext,), attrs)
    mod = types.SimpleNamespace(LAContext=ctx, LAPolicyDeviceOwnerAuthentication=2)
    monkeypatch.setitem(sys.modules, "LocalAuthentication", mod)


@pytest.mark.parametrize("value", [1, "yes", object(), [True], None, False])
def test_only_a_real_true_from_the_reply_passes(
    value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_la(monkeypatch, reply_with=value)
    assert REAL_CONFIRM() is False


def test_a_true_reply_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_la(monkeypatch, reply_with=True)
    assert REAL_CONFIRM() is True


def test_no_reply_times_out_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_la(monkeypatch, silent=True)
    monkeypatch.setattr(touch_id, "_WAIT_S", 0.01)
    assert REAL_CONFIRM() is False


def test_local_authentication_raising_never_opens_the_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_la(monkeypatch, raises=True)
    monkeypatch.setattr(touch_id, "confirm", REAL_CONFIRM)
    opened: list[bool] = []  # main() swallows exceptions, so record instead

    def open_db(**_: object) -> None:
        opened.append(True)

    monkeypatch.setattr(cli, "open_db", open_db)
    assert cli.main(["up"]) == 1
    assert opened == []
