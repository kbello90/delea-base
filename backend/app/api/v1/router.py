"""Aggregates the v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    automation,
    customers,
    dashboard,
    expense_receipts,
    expenses,
    invoices,
    materials,
    members,
    password_reset,
    product_images,
    products,
    public,
    settings,
    stripe_webhooks,
)

api_router = APIRouter()
for module in (
    products,
    product_images,
    materials,
    customers,
    invoices,
    expenses,
    expense_receipts,
    automation,
    settings,
    members,
    password_reset,
    ai,
    dashboard,
    public,
    stripe_webhooks,
):
    api_router.include_router(module.router)
