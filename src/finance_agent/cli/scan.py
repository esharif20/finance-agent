"""`fa scan`: build the Assistant's profile over all history now (#327), with a
progress line. Idempotent; the running `fa` server does the same on start and
after each sync. Figures go to the user's own terminal only (`--show`)."""

import time
from collections.abc import Callable

from sqlalchemy import Engine

from finance_agent.engine import profile

CHARS_PER_TOKEN = 4  # as assistant.context counts them


def scan(
    engine: Engine,
    user_id: int,
    show_text: bool = False,
    out: Callable[[str], object] = print,
) -> int:
    out("Scanning your history...")
    t0 = time.monotonic()
    profile.forget(user_id)
    with engine.begin() as conn:
        text, p = profile.profile_context(conn, user_id)
    took = time.monotonic() - t0
    tokens = -(-len(text) // CHARS_PER_TOKEN)
    out(
        f"Profile ready: {p.months} months, {len(p.accounts)} accounts, "
        f"{len(p.bills)} bills and subscriptions, about {tokens} tokens, in {took:.1f}s"
    )
    if show_text:
        out(text)
    return 0
