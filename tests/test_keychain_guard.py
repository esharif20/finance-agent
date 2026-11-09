"""Tests never reach the real macOS Keychain, in this process or in a subprocess."""

import subprocess
import sys

import keyring


def test_this_process_uses_the_in_memory_keyring() -> None:
    assert type(keyring.get_keyring()).__name__ == "MemoryKeyring"


def test_a_spawned_python_gets_a_keyring_that_refuses() -> None:
    probe = (
        "import keyring, keyring.errors\n"
        "print(type(keyring.get_keyring()).__module__)\n"
        "try:\n"
        "    keyring.get_password('finance-agent', 'probe')\n"
        "    print('reached')\n"
        "except keyring.errors.NoKeyringError:\n"
        "    print('refused')\n"
    )
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.split()
    assert out == ["keyring.backends.fail", "refused"]
