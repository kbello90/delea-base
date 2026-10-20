"""Unit tests for the CSV helpers: formula-injection escaping and exact money formatting."""

import asyncio
import csv
import io
from datetime import UTC, date, datetime

import pytest

from app.core.csv_export import cents_to_decimal, csv_line, csv_stream, safe_cell


@pytest.mark.parametrize(
    "dangerous",
    ["=1+1", "+1", "-1", "@SUM(A1)", "\tcmd", "\rcmd", '=HYPERLINK("http://x","y")', "-"],
)
def test_formula_prefixes_get_a_quote(dangerous: str) -> None:
    assert safe_cell(dangerous) == "'" + dangerous


@pytest.mark.parametrize("plain", ["Packaging boxes", "a=b", "12.50", "x-1", " =padded", ""])
def test_plain_text_is_unchanged(plain: str) -> None:
    assert safe_cell(plain) == plain


def test_non_string_values() -> None:
    assert safe_cell(None) == ""
    assert safe_cell(True) == "true" and safe_cell(False) == "false"
    assert safe_cell(date(2026, 10, 2)) == "2026-10-02"
    assert safe_cell(datetime(2026, 10, 2, 9, 30, tzinfo=UTC)) == "2026-10-02T09:30:00+00:00"
    assert safe_cell(-5) == "'-5"  # negative numbers are escaped too; amounts here are >= 0


@pytest.mark.parametrize(
    ("cents", "text"),
    [(0, "0.00"), (5, "0.05"), (99, "0.99"), (100, "1.00"), (1234, "12.34"),
     (9223372036854775807, "92233720368547758.07"), (-150, "-1.50"), (None, None)],
)  # fmt: skip
def test_cents_to_decimal_is_exact(cents: int | None, text: str | None) -> None:
    assert cents_to_decimal(cents) == text


def test_csv_line_quotes_commas_quotes_and_newlines() -> None:
    line = csv_line(["a,b", 'say "hi"', "two\nlines", "=bad"])
    assert next(csv.reader(io.StringIO(line))) == ["a,b", 'say "hi"', "two\nlines", "'=bad"]
    assert line.endswith("\r\n")


def test_csv_stream_writes_header_then_rows() -> None:
    async def rows():
        yield ("2026-10-02", "+fee", None)
        yield ("2026-10-03", "ok", "12.00")

    async def collect() -> str:
        return "".join([chunk async for chunk in csv_stream(("d", "desc", "amount"), rows())])

    parsed = list(csv.reader(io.StringIO(asyncio.run(collect()))))
    assert parsed == [
        ["d", "desc", "amount"],
        ["2026-10-02", "'+fee", ""],
        ["2026-10-03", "ok", "12.00"],
    ]
