"""CSV export helpers.

CSV safety: a cell starting with = + - @ tab or CR can be executed as a formula by spreadsheet
apps (CSV injection). Every such cell is prefixed with a single quote. Money is written as an
exact decimal string from integer cents; no floats are involved.
"""

import csv
import io
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import date, datetime
from typing import Any

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime | date):
        return value.isoformat()
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def cents_to_decimal(cents: int | None) -> str | None:
    """12345 -> "123.45". Exact for any integer; negative values keep their sign."""
    if cents is None:
        return None
    sign = "-" if cents < 0 else ""
    whole, part = divmod(abs(cents), 100)
    return f"{sign}{whole}.{part:02d}"


def csv_line(values: Iterable[Any]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerow([safe_cell(v) for v in values])
    return buffer.getvalue()


async def csv_stream(
    header: Sequence[str], rows: AsyncIterator[Sequence[Any]]
) -> AsyncIterator[str]:
    yield csv_line(header)
    async for row in rows:
        yield csv_line(row)
