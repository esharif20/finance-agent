import importlib

import pytest

MODULES = [
    "storage",
    "ingest",
    "ledger",
    "categorise",
    "engine",
    "memory",
    "assistant",
    "gateway",
    "api",
    "cli",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_importable(name: str) -> None:
    importlib.import_module(f"finance_agent.{name}")
