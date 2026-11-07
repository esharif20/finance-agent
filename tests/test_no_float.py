"""Lint rule: no `float` in ledger, categorise or api (money is Decimal/minor units)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src/finance_agent"
GUARDED = ["ledger", "categorise", "api"]


def float_uses(source: str) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Name) and node.id == "float") or (
            isinstance(node, ast.Constant) and type(node.value) is float
        ):
            lines.append(node.lineno)
    return sorted(lines)


@pytest.mark.parametrize("module", GUARDED)
def test_no_float_in_money_modules(module: str) -> None:
    offenders = [
        f"{p.relative_to(SRC)}:{line}"
        for p in (SRC / module).rglob("*.py")
        for line in float_uses(p.read_text())
    ]
    assert offenders == []


def test_the_rule_catches_floats() -> None:
    assert float_uses("x = float(y)\nz = 0.1\nn = 1\n") == [1, 2]
