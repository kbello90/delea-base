"""Invoice routes, including order_status transitions (TRD §3.1). Stub."""

from fastapi import APIRouter

router = APIRouter(prefix="/invoices", tags=["invoices"])
