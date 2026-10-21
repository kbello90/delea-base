"""Pure helpers for building invoices (no database). Pricing lives in app/services/pricing.py."""

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

WALK_IN_NAME = "Walk-in customer"

_NET_DAYS = {"net_15": 15, "net_30": 30, "net_60": 60}


def customer_display_name(customer: Mapping[str, Any] | None) -> str:
    """The name snapshotted on the invoice: the company for a wholesale customer, otherwise
    'first last'. Walk-in sales (no customer) get a fixed label."""
    if customer is None:
        return WALK_IN_NAME
    if customer["customer_type"] == "wholesale" and customer.get("company_name"):
        return customer["company_name"]
    parts = [customer.get("first_name"), customer.get("last_name")]
    return " ".join(p for p in parts if p) or WALK_IN_NAME


def due_date_for(payment_terms: str | None, today: date) -> date | None:
    """net_15/30/60 -> today + N days; due_on_receipt or no customer -> no due date."""
    days = _NET_DAYS.get(payment_terms or "")
    return today + timedelta(days=days) if days else None
