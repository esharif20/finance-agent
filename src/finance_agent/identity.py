"""Shared by `cli` and `api`, which never import each other."""

HOST = "127.0.0.1"  # never 0.0.0.0: nothing on the network may read the Ledger
DEFAULT_PORT = 8765


def current_user() -> int:
    """The single local user until the demo milestone (ADR 0003)."""
    return 1
