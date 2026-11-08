"""Record synthetic Gemini replies into tests/fixtures/synthetic/cloud/ (#298).

`uv run python scripts/record_cloud.py --record` sends one synthetic payload to
each configured model with the user's Keychain key, on the user's machine only.
CI never runs it: it has no key, and the script refuses when `CI` is set."""

import argparse
import os
import sys
from pathlib import Path

from finance_agent.gateway import Money, ToolFact, redact
from finance_agent.gateway.cloud import (
    FLASH_MODEL,
    PRO_MODEL,
    CloudChatModel,
    RecordingTransport,
)
from finance_agent.storage import keychain

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "synthetic" / "cloud"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true", help="call Gemini for real")
    if not parser.parse_args().record:
        parser.error("pass --record to call Gemini with your key")
    key = keychain.gemini_key()
    if os.environ.get("CI") or key is None:
        sys.exit("recording needs the user's Keychain key and never runs in CI")
    payload, _ = redact(
        "How much did I spend on groceries last month?",
        [ToolFact(field="spending", value=Money(minor=4250))],
    )
    cloud = CloudChatModel(key, transport=RecordingTransport(OUT, key))
    for model in (FLASH_MODEL, PRO_MODEL):
        _, usage = cloud.complete(payload, model=model, max_tokens=256)
        print(model, usage.model_version)  # no reply text: a habit, not a secret
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
