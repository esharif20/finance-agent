"""The one real-key run of evals/cloud_synth.py (#302, #306).

    uv run python scripts/cloud_synth_live.py --run

Prints the worst-case cost first, then sends only synthetic taxonomy names,
example synthetic descriptors and golden questions to Gemini Flash, metered on
the `synthetic_judge` bucket (it stops at the cap). It replaces the offline
recordings with the real replies (`synth_*.json`), rebuilds the datasets and
refreshes the categoriser results. Never runs in CI."""

import os
import subprocess
import sys

import cloud_synth
from finance_agent.gateway.cloud import FLASH_MODEL, CloudChatModel, urllib_transport
from finance_agent.storage import default_db_path, keychain
from finance_agent.storage.spend import SpendStore


def main() -> int:
    if "--run" not in sys.argv:
        sys.exit("pass --run to call Gemini with your Keychain key")
    key = keychain.gemini_key()
    if os.environ.get("CI") or key is None:
        sys.exit("needs the user's Keychain key and never runs in CI")
    reqs = cloud_synth.requests()
    print(
        f"cloud_synth live: {len(reqs)} calls on {FLASH_MODEL}; worst case "
        f"{cloud_synth.estimate(reqs)} pence, typically a fraction of that",
        flush=True,
    )
    out = cloud_synth.RECORDINGS
    for p in out.glob("synth_*.json"):
        p.unlink()
    transport = cloud_synth.writing_transport(out, urllib_transport, key)
    res = cloud_synth.generate(
        CloudChatModel(key, transport=transport),
        reqs,
        SpendStore.beside(default_db_path()),
    )
    cloud_synth.write(res)
    print(
        f"{len(res.descriptors)} descriptors, {len(res.paraphrases)} paraphrases, "
        f"{res.calls} calls, {res.pence} pence, dropped {dict(res.dropped)}"
        + (", STOPPED AT THE SPEND CAP" if res.stopped_at_cap else "")
    )
    cmd = ["uv", "run", "python", "evals/categoriser.py", "--write"]
    return subprocess.call(cmd, cwd=cloud_synth.HERE.parent)  # noqa: S603


if __name__ == "__main__":
    raise SystemExit(main())
