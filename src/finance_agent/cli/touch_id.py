# pyright: reportMissingImports=false, reportMissingTypeStubs=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportUnknownVariableType=false
"""Touch ID (or the Mac password) before `fa` reads the Keychain and opens the
app (#352). A gate in front of the reads, not a Keychain access control: an
unsigned CLI can't put items in the data-protection Keychain behind biometry.
It stops someone at an unlocked Mac, not malware running as the user."""

import threading

REASON = "open Finance Agent"
_WAIT_S = 120.0  # the system sheet times out on its own; this only bounds a hang


def confirm(reason: str = REASON) -> bool:
    """True only when the user passed Touch ID or typed the Mac password.
    Anything else (cancelled, no LocalAuthentication, a hang) is False."""
    try:
        from LocalAuthentication import (
            LAContext,
            LAPolicyDeviceOwnerAuthentication,
        )
    except ImportError:
        return False
    ctx = LAContext.new()
    ok, _ = ctx.canEvaluatePolicy_error_(LAPolicyDeviceOwnerAuthentication, None)
    if not ok:
        return False
    done = threading.Event()
    result: list[bool] = []

    def reply(success: object, _error: object) -> None:
        result.append(success is True)
        done.set()

    ctx.evaluatePolicy_localizedReason_reply_(
        LAPolicyDeviceOwnerAuthentication, reason, reply
    )
    if not done.wait(_WAIT_S):
        ctx.invalidate()
        return False
    return result == [True]
