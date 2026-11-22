"""Reusable data checks. Each returns a list of human-readable failures (empty list = pass)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterable

EXCEL_ERRORS = ("#N/A", "#REF!", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!")


def no_excel_errors(rows: Iterable[Iterable[Any]]) -> list[str]:
    failures = []
    for i, row in enumerate(rows, start=1):
        for cell in row:
            if isinstance(cell, str) and cell.strip().upper() in EXCEL_ERRORS:
                failures.append(f"Row {i} contains Excel error {cell}")
    return failures


def as_of_matches(cell_value: Any, as_of: date) -> list[str]:
    value = cell_value.date() if isinstance(cell_value, datetime) else cell_value
    if value != as_of:
        return [f"Workbook as-of date {value} does not match business date {as_of}"]
    return []


def ties_to_control(values: Iterable[float], control_total: float, tolerance: float = 1.0) -> list[str]:
    total = sum(values)
    diff = total - control_total
    if abs(diff) > tolerance:
        return [f"Totals do not tie to control cell (difference {diff:,.0f})"]
    return []


def expected_header(header: list[Any], expected: list[str]) -> list[str]:
    got = [str(h).strip().lower() if h is not None else "" for h in header]
    if got != [e.lower() for e in expected]:
        return [f"Unexpected columns {got}; expected {expected}"]
    return []


def row_count_within(today: int, prior: int | None, max_change: float = 0.2) -> list[str]:
    if prior in (None, 0):
        return []
    change = abs(today - prior) / prior
    if change > max_change:
        return [f"Row count changed by {change:.0%} versus the prior day ({prior} -> {today})"]
    return []
