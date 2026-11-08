"""Monthly facts checker (#290). Runs in CI only; the app never fetches (ADR 0013).

Fetches a fixed, code-reviewed allowlist of official pages, reads each value with a
page-specific regex (no LLM), and rewrites facts.json when a value changed. Prints a
JSON report of changes and failures; the workflow turns those into a PR or issues.
Only facts already present in facts.json are checked.
"""

import json
import re
import sys
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

FACTS = (
    Path(__file__).resolve().parent.parent
    / "src/finance_agent/engine/playbook_data/facts.json"
)

# Allowlist: fact id -> (url, regex, group 1 = pounds). Edit only via reviewed PR.
SOURCES: dict[str, tuple[str, str]] = {
    "isa_allowance": (
        "https://www.gov.uk/individual-savings-accounts",
        r"save up to £([\d,]+) in ISAs",
    ),
    "fscs_deposit_limit": (
        "https://www.fscs.org.uk/check/what-are-we-protecting/",
        r"protected up to £([\d,]+) per person",
    ),
    "personal_allowance": (
        "https://www.gov.uk/income-tax-rates",
        r"Personal Allowance</td>\s*<td>(?:Up to )?£([\d,]+)",
    ),
    "student_loan_plan2_threshold": (
        "https://www.gov.uk/repaying-your-student-loan/what-you-pay",
        r"Plan 2</td>\s*<td>£([\d,]+)",
    ),
}


def fetch(url: str) -> str | None:
    if not url.startswith("https://"):
        return None
    try:
        with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 - https allowlist only
            return r.read().decode("utf-8", "replace")
    except Exception:
        return None


def parse(fact_id: str, html: str) -> int | None:
    m = re.search(SOURCES[fact_id][1], html)
    return int(m.group(1).replace(",", "")) * 100 if m else None


def check(
    facts_path: Path, fetcher: Callable[[str], str | None], today: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    data = cast(dict[str, Any], json.loads(facts_path.read_text()))
    changes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    fact: dict[str, Any]
    for fact in data["facts"]:
        if fact["id"] not in SOURCES:
            continue
        url = SOURCES[fact["id"]][0]
        html = fetcher(url)
        new = parse(fact["id"], html) if html is not None else None
        if new is None:
            reason = "unreachable" if html is None else "parser found no value"
            failures.append({"id": fact["id"], "source": url, "reason": reason})
            continue
        if new != fact["value"]:
            changes.append(
                {
                    "id": fact["id"],
                    "source": url,
                    "old": fact["value"],
                    "new": new,
                    "date": today.isoformat(),
                }
            )
            fact["value"] = new
        fact["checked_on"] = today.isoformat()
    facts_path.write_text(json.dumps(data, indent=2) + "\n")
    return changes, failures


if __name__ == "__main__":
    ch, fl = check(FACTS, fetch, date.today())
    json.dump({"changes": ch, "failures": fl}, sys.stdout, indent=2)
    print()
