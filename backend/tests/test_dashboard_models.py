"""Unit tests for the dashboard trend: service result shape and response model (no database)."""

from datetime import date
from typing import Any

import pytest

from app.api.v1.dashboard import MonthlyTrend
from app.services.dashboard import MONTHS_IN_TREND, fetch_monthly


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: tuple[str, dict[str, Any]] | None = None

    async def __aenter__(self) -> "_Cursor":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: dict[str, Any]) -> None:
        self.executed = (sql, params)

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Conn:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self, row_factory: object = None) -> _Cursor:
        return self._cursor


@pytest.mark.anyio
async def test_fetch_monthly_shape_and_params() -> None:
    rows = [
        {"month_start": date(2026, m, 1), "revenue_cents": m * 100, "expenses_cents": 0}
        for m in range(5, 11)
    ]
    cursor = _Cursor(rows)
    result = await fetch_monthly(_Conn(cursor), "11111111-1111-1111-1111-111111111111")  # type: ignore[arg-type]

    assert cursor.executed is not None
    sql, params = cursor.executed
    assert params == {"tenant_id": "11111111-1111-1111-1111-111111111111", "back": 5}
    assert "refunded_at is null" in sql and "cancelled_at is null" in sql
    assert MONTHS_IN_TREND == 6

    trend = MonthlyTrend.model_validate(result)
    assert trend.currency_unit == "cents" and trend.period_basis == "UTC"
    assert [m.month_start.isoformat() for m in trend.months][-1] == "2026-10-01"
    assert trend.months[0].revenue_cents == 500


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
