"""Small table-printing compatibility layer.

The GIS skill should work in constrained Codex macOS sandboxes where the
system Python already has GeoPandas/Pyogrio but not every presentation
dependency. Prefer python-tabulate when available and fall back to a simple
plain-text formatter when it is not installed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

try:
    from tabulate import tabulate as _tabulate
except ModuleNotFoundError:
    _tabulate = None


def _records(rows: Any, headers: Any, showindex: bool) -> tuple[list[str], list[list[str]]]:
    if hasattr(rows, "to_dict"):
        data = rows.reset_index() if showindex else rows
        records = data.to_dict("records")
    elif isinstance(rows, Mapping):
        records = [rows]
    else:
        records = list(rows)

    if not records:
        if headers == "keys":
            return [], []
        return [str(h) for h in headers] if headers else [], []

    first = records[0]
    if isinstance(first, Mapping):
        if headers == "keys":
            columns = list(first.keys())
        else:
            columns = list(headers)
        body = [[_format_value(record.get(col, "")) for col in columns] for record in records]
        return [str(c) for c in columns], body

    body = [[_format_value(value) for value in row] for row in records]
    return [str(h) for h in headers] if headers and headers != "keys" else [], body


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return "" if value is None else str(value)


def _fallback_table(rows: Any, headers: Any = (), showindex: bool = True, **_: Any) -> str:
    columns, body = _records(rows, headers, showindex)
    if not columns:
        return "\n".join("  ".join(row) for row in body)

    widths = [len(col) for col in columns]
    for row in body:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]

    header = "  ".join(col.ljust(width) for col, width in zip(columns, widths))
    rule = "  ".join("-" * width for width in widths)
    lines = [header, rule]
    lines.extend("  ".join(cell.ljust(width) for cell, width in zip(row, widths)) for row in body)
    return "\n".join(lines)


def tabulate(rows: Iterable[Any], *args: Any, **kwargs: Any) -> str:
    if _tabulate is not None:
        return _tabulate(rows, *args, **kwargs)
    return _fallback_table(rows, *args, **kwargs)
