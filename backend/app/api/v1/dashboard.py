"""Dashboard routes.

Owner only: under RLS, staff can't see expenses and CPA can't see products, so their numbers
would be silently partial. Computed in SQL in the caller's user session (RLS applies), with
an explicit tenant filter. Formulas: app/services/dashboard.py.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentClaims, DbConn, require_role
from app.services.dashboard import fetch_summary

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class PeriodSummary(BaseModel):
    revenue_cents: int
    tax_reserve_cents: int
    expenses_cents: int
    deductible_expenses_cents: int
    net_profit_cents: int


class InvoiceCounts(BaseModel):
    quote: int
    paid: int
    in_production: int
    shipped: int
    delivered: int
    refunded: int
    cancelled: int


class DashboardSummary(BaseModel):
    currency_unit: Literal["cents"]
    period_basis: Literal["UTC"]
    month_start: datetime
    year_start: datetime
    mtd: PeriodSummary
    ytd: PeriodSummary
    low_stock_products: int
    low_stock_materials: int
    invoice_counts: InvoiceCounts


@router.get(
    "/summary", response_model=DashboardSummary, dependencies=[Depends(require_role("owner"))]
)
async def dashboard_summary(claims: CurrentClaims, conn: DbConn) -> DashboardSummary:
    return DashboardSummary.model_validate(await fetch_summary(conn, claims.tenant_id))
