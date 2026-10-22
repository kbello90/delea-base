"""Unit tests for the invoice models and helpers (no database)."""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.models.invoice import (
    InvoiceCreate,
    InvoiceItemsReplace,
    InvoicePreview,
    InvoiceStatusChange,
    InvoiceUpdate,
    PaymentMethod,
)
from app.services.invoices import WALK_IN_NAME, customer_display_name, due_date_for

ITEM = {"product_id": str(uuid.uuid4()), "qty": 1}


def test_minimal_create_is_a_walk_in_quote() -> None:
    body = InvoiceCreate(items=[ITEM])
    assert body.customer_id is None and body.pay_now is False and body.tax_rate_bps is None


@pytest.mark.parametrize(
    "forged",
    [
        {"subtotal_cents": 1},
        {"tax_cents": 0},
        {"total_cents": 1},
        {"order_status": "paid"},
        {"status": "paid"},
        {"quote_number": "QTE-1"},
        {"invoice_number": "INV-1"},
        {"paid_at": "2026-10-20T00:00:00Z"},
        {"tenant_id": str(uuid.uuid4())},
        {"customer_name": "Someone"},
        {"source": "storefront"},
    ],
)
def test_server_computed_fields_cannot_be_sent(forged: dict) -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate(items=[ITEM], **forged)


@pytest.mark.parametrize(
    "item",
    [
        {**ITEM, "unit_price_cents": 1},
        {**ITEM, "line_total_cents": 1},
        {**ITEM, "description": "x"},
        {**ITEM, "qty": 0},
        {**ITEM, "qty": 1000},
        {**ITEM, "qty": 1.5},
        {"qty": 1},
    ],
)
def test_line_items_take_only_product_and_qty(item: dict) -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate(items=[item])


def test_line_count_bounds_and_duplicates() -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate(items=[])
    with pytest.raises(ValidationError):
        InvoiceCreate(items=[ITEM] * 101)
    assert len(InvoiceCreate(items=[ITEM] * 100).items) == 100  # duplicates allowed
    with pytest.raises(ValidationError):
        InvoiceItemsReplace(items=[ITEM], total_cents=5)


@pytest.mark.parametrize("bps", [-1, 10001, 8.5])
def test_tax_override_bounds(bps) -> None:
    with pytest.raises(ValidationError):
        InvoiceCreate(items=[ITEM], tax_rate_bps=bps)


def test_status_change_takes_only_a_valid_status() -> None:
    assert InvoiceStatusChange(status="shipped").status == "shipped"
    for bad in ({"status": "refunded"}, {"status": "paid", "paid_at": "2026-01-01"}, {}):
        with pytest.raises(ValidationError):
            InvoiceStatusChange(**bad)


def test_customer_display_name() -> None:
    assert customer_display_name(None) == WALK_IN_NAME
    assert (
        customer_display_name(
            {"customer_type": "retail", "first_name": "Sarah", "last_name": "Jenkins"}
        )
        == "Sarah Jenkins"
    )
    assert (
        customer_display_name({"customer_type": "retail", "first_name": "Cher", "last_name": None})
        == "Cher"
    )
    assert (
        customer_display_name(
            {
                "customer_type": "wholesale",
                "first_name": "Elena",
                "last_name": "Rossi",
                "company_name": "Bella Boutique",
            }
        )
        == "Bella Boutique"
    )


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        ("net_15", date(2026, 11, 4)),
        ("net_30", date(2026, 11, 19)),
        ("net_60", date(2026, 12, 19)),
        ("due_on_receipt", None),
        (None, None),
    ],
)
def test_due_date_from_payment_terms(terms: str | None, expected: date | None) -> None:
    assert due_date_for(terms, date(2026, 10, 20)) == expected


def test_payment_method_values_match_the_enum() -> None:
    assert [m.value for m in PaymentMethod] == ["cash", "card", "bank_transfer", "other"]
    with pytest.raises(ValidationError):
        InvoiceCreate(items=[ITEM], pay_now=True, payment_method="cheque")
    change = InvoiceStatusChange(status="paid", payment_method="cash")
    assert change.payment_method == PaymentMethod.cash
    assert InvoiceStatusChange(status="shipped").payment_method is None


def test_preview_takes_what_create_prices_and_nothing_else() -> None:
    body = InvoicePreview(items=[ITEM], tax_rate_bps=500)
    assert body.customer_id is None and body.tax_rate_bps == 500
    for forged in (
        {"pay_now": True},
        {"notes": "x"},
        {"total_cents": 1},
        {"due_date": "2027-01-01"},
    ):
        with pytest.raises(ValidationError):
            InvoicePreview(items=[ITEM], **forged)


def test_invoice_update_takes_notes_due_date_and_payment_method_only() -> None:
    assert InvoiceUpdate(notes=None).model_dump(exclude_unset=True) == {"notes": None}
    assert InvoiceUpdate(payment_method="card").payment_method == PaymentMethod.card
    for forged in (
        {"total_cents": 1},
        {"order_status": "paid"},
        {"customer_id": str(uuid.uuid4())},
        {"tax_rate_bps": 0},
        {"items": [ITEM]},
        {"notes": "x" * 5001},
    ):
        with pytest.raises(ValidationError):
            InvoiceUpdate(**forged)
