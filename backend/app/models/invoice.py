"""Invoice schemas. Mirrors public.invoices / invoice_line_items (Schema §5); money is integer
cents and is always computed by the server (app/services/pricing.py).

Request models forbid unknown fields, so a client can never send totals, prices, status,
numbers or tenant_id: any of them is a 422.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrderStatus(StrEnum):
    """Matches the `order_status` enum; declaration order is the forward order."""

    quote = "quote"
    paid = "paid"
    in_production = "in_production"
    shipped = "shipped"
    delivered = "delivered"


class PaymentMethod(StrEnum):
    """Matches the `payment_method` enum. Recorded when an invoice is paid; null on quotes and
    on invoices paid before it existed."""

    cash = "cash"
    card = "card"
    bank_transfer = "bank_transfer"
    other = "other"


class LineItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    qty: Annotated[int, Field(ge=1, le=999)]


Items = Annotated[list[LineItemIn], Field(min_length=1, max_length=100)]


class InvoiceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None  # none: walk-in sale
    items: Items
    notes: Annotated[str, Field(max_length=5000)] | None = None
    due_date: date | None = None  # default: from the customer's payment terms
    tax_rate_bps: Annotated[int, Field(ge=0, le=10000)] | None = None  # owner-only override
    pay_now: bool = False  # in-store sale: move to paid in the same transaction
    payment_method: PaymentMethod | None = None  # required with pay_now, refused without


class InvoicePreview(BaseModel):
    """What the client is about to create: priced without writing anything."""

    model_config = ConfigDict(extra="forbid")

    customer_id: UUID | None = None
    items: Items
    tax_rate_bps: Annotated[int, Field(ge=0, le=10000)] | None = None  # owner-only override


class InvoiceItemsReplace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: Items


class InvoiceStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: OrderStatus
    # Required when the invoice leaves quote (that is when it is paid), refused otherwise.
    payment_method: PaymentMethod | None = None


class InvoiceUpdate(BaseModel):
    """PATCH: notes (any status), due date (quotes only), payment method (owner, once paid)."""

    model_config = ConfigDict(extra="forbid")

    notes: Annotated[str, Field(max_length=5000)] | None = None
    due_date: date | None = None
    payment_method: PaymentMethod | None = None


INVOICE_NULLABLE_FIELDS = frozenset({"notes", "due_date"})


class InvoiceLineOut(BaseModel):
    id: UUID
    product_id: UUID | None
    description: str
    qty: int
    unit_price_cents: int
    line_total_cents: int


class PreviewLineOut(BaseModel):
    product_id: UUID
    description: str
    qty: int
    unit_price_cents: int
    line_total_cents: int


class StockShortage(BaseModel):
    """A product the lines need more of than is in stock now (summed across lines)."""

    product_id: UUID
    sku: str
    available: int
    needed: int


class InvoicePreviewOut(BaseModel):
    lines: list[PreviewLineOut]
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int
    stock_shortages: list[StockShortage]


class InvoiceOut(BaseModel):
    id: UUID
    tenant_id: UUID
    quote_number: str | None
    invoice_number: str | None
    order_status: OrderStatus
    customer_id: UUID | None
    customer_name: str
    source: str
    subtotal_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int
    due_date: date | None
    notes: str | None
    payment_method: PaymentMethod | None
    paid_at: datetime | None
    refunded_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # None when the caller can't read line items (cpa) or on list endpoints; never [] for that.
    items: list[InvoiceLineOut] | None = None
