"""Tool results -> grounding `ToolValue`s (#139; conventions in docs/AI_DESIGN.md).

Every figure a tool returned becomes a value with the nearest evidence ID:
money as signed minor units, `YYYY-MM` as a month, ISO dates as dates, and
whole numbers as counts. Signs stay exact; only a Spending Transaction row
also gives its positive outflow, so "you spent £42.50" grounds on a -£42.50
row. Identifiers and flags are not figures. Bank text (`{"data": ...}`) is
never read for figures."""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel

from finance_agent.assistant.grounding import Money, Month, ToolValue

_MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NOT_FIGURES = {"evidence_id", "truncated", "incomplete", "minor", "currency"}


def tool_values(tool: str, out: BaseModel) -> list[ToolValue]:
    found: list[ToolValue] = []
    _walk(out.model_dump(), getattr(out, "evidence_id", tool), tool, found)
    return found


def _walk(node: Any, evidence: str, label: str, found: list[ToolValue]) -> None:
    if isinstance(node, list):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            _walk(item, evidence, label, found)
        return
    if isinstance(node, dict):
        d: dict[str, Any] = node  # pyright: ignore[reportUnknownVariableType]
        if set(d) == {"data"}:
            return  # bank or user text: never a figure
        evidence = d.get("evidence_id", evidence)
        name = next((d[k] for k in ("name", "month", "posted_on") if k in d), None)
        if isinstance(name, str):
            label = f"{label} {name}"
        if set(d) == {"minor", "currency"}:
            found.append(_value(evidence, label, Money(**d)))
            return
        for k, v in d.items():
            if k in _NOT_FIGURES or k.endswith("_id"):
                continue
            _walk(v, evidence, f"{label} {k}", found)
        amount = d.get("amount")
        if d.get("kind") == "Spending" and "posted_on" in d and amount:
            outflow = Money(minor=-amount["minor"], currency=amount["currency"])
            if outflow.minor > 0:
                found.append(_value(evidence, f"{label} outflow", outflow))
        return
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, int):
        found.append(_value(evidence, label, node))
    elif isinstance(node, date):
        found.append(_value(evidence, label, node))
    elif isinstance(node, str):
        if _MONTH.fullmatch(node):
            found.append(
                _value(evidence, label, Month(year=int(node[:4]), month=int(node[5:])))
            )
        elif _DATE.fullmatch(node):
            try:
                found.append(_value(evidence, label, date.fromisoformat(node)))
            except ValueError:
                pass


def _value(evidence: str, label: str, v: Any) -> ToolValue:
    return ToolValue(evidence_id=evidence, label=label.strip(), value=v)
