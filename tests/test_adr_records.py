"""ADR records: contiguous numbers, real successors, egress rule cites its ADR."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"


def _adrs() -> dict[int, Path]:
    adrs: dict[int, Path] = {}
    for path in sorted(ADR_DIR.glob("*.md")):
        match = re.match(r"(\d{4})-", path.name)
        assert match, f"ADR file without a 4-digit number: {path.name}"
        number = int(match.group(1))
        assert number not in adrs, f"duplicate ADR number {number:04d}"
        adrs[number] = path
    return adrs


def _status(path: Path) -> str:
    match = re.search(r"^status:\s*(.+)$", path.read_text(), re.MULTILINE)
    assert match, f"{path.name} has no status"
    return match.group(1).strip()


def test_adr_numbers_unique_and_contiguous() -> None:
    numbers = sorted(_adrs())
    assert numbers == list(range(1, len(numbers) + 1))


def test_superseded_adrs_name_an_existing_later_successor() -> None:
    adrs = _adrs()
    for number, path in adrs.items():
        status = _status(path)
        if not status.startswith("superseded"):
            continue
        match = re.fullmatch(r"superseded by ADR (\d{4})", status)
        assert match, f"{path.name}: bad superseded status {status!r}"
        successor = int(match.group(1))
        assert successor in adrs, f"{path.name}: missing successor {successor:04d}"
        assert successor > number, f"{path.name}: successor must be a later ADR"
        assert _status(adrs[successor]) == "accepted"
        assert f"Supersedes ADR {number:04d}" in adrs[successor].read_text()


def test_agents_egress_rule_cites_the_egress_adr() -> None:
    lines = (ROOT / "AGENTS.md").read_text().splitlines()
    rule = next(line for line in lines if "No network egress" in line)
    cited = [int(n) for n in re.findall(r"ADR (\d{4})", rule)]
    assert cited, "AGENTS.md egress rule cites no ADR"
    adrs = _adrs()
    for number in cited:
        assert number in adrs
        text = adrs[number].read_text()
        assert _status(adrs[number]) == "accepted"
        # each cited ADR names the host it allows (0013 SimpleFIN, 0016 Gemini)
        assert (
            "Egress is allowed to exactly one SimpleFIN host" in text
            or "generativelanguage.googleapis.com" in text
        )
