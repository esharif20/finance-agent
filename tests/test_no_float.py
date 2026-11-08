"""Lint rule: no `float` in ledger, categorise, engine or api (money is Decimal or
minor units). The exceptions are the engine model files below, which fit models in
float and return integer pence for every money output (#149, #252, ARCHITECTURE.md)."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src/finance_agent"
GUARDED = ["ledger", "categorise", "engine", "api"]
# engine/ only: model internals (ETS, Bradley-Terry, Beta draws, change-point, LP, RRF)
FLOAT_ALLOWED = (
    "forecast*.py",
    "preferences*.py",
    "bandit*.py",
    "changepoint*.py",
    "allocation*.py",
    "search*.py",
)


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
        if not (module == "engine" and any(p.match(g) for g in FLOAT_ALLOWED))
        for line in float_uses(p.read_text())
    ]
    assert offenders == []


def test_the_rule_catches_floats() -> None:
    assert float_uses("x = float(y)\nz = 0.1\nn = 1\n") == [1, 2]


def test_only_the_named_model_files_may_use_float() -> None:
    assert not any(Path("engine/insights.py").match(g) for g in FLOAT_ALLOWED)
    assert Path("engine/changepoint.py").match("changepoint*.py")
