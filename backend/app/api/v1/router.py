"""Aggregates the v1 routers."""

from fastapi import APIRouter

from app.api.v1 import (
    ai,
    automation,
    customers,
    dashboard,
    expenses,
    invoices,
    materials,
    products,
    settings,
    stripe_webhooks,
)

api_router = APIRouter()
for module in (
    products,
    materials,
    customers,
    invoices,
    expenses,
    automation,
    settings,
    ai,
    dashboard,
    stripe_webhooks,
):
    api_router.include_router(module.router)
