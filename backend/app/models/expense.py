"""Expense schemas. Mirrors public.expenses (Schema §5); money is integer cents.

Create and update bodies forbid unknown fields, so receipt_* columns, tenant_id and ids can
never be sent: receipts change only through the receipt endpoints.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.product import Cents
from app.services.receipts import ReceiptType

Description = Annotated[str, Field(min_length=1, max_length=500)]


class ExpenseCategory(StrEnum):
    """Matches the `expense_category` enum (migration 20261023000100)."""

    materials = "materials"
    packaging = "packaging"
    shipping = "shipping"
    marketing = "marketing"
    platform_fees = "platform_fees"
    software = "software"
    equipment = "equipment"
    rent = "rent"
    utilities = "utilities"
    phone = "phone"
    insurance = "insurance"
    professional_services = "professional_services"
    taxes_licenses = "taxes_licenses"
    travel = "travel"
    meals = "meals"
    other = "other"


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_date: date
    description: Description
    category: ExpenseCategory = ExpenseCategory.other
    amount_cents: Cents
    is_tax_deductible: bool = True


class ExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_date: date | None = None
    description: Description | None = None
    category: ExpenseCategory | None = None
    amount_cents: Cents | None = None
    is_tax_deductible: bool | None = None


NULLABLE_FIELDS: frozenset[str] = frozenset()  # every expense field is required


class ExpenseOut(BaseModel):
    id: UUID
    tenant_id: UUID
    expense_date: date
    description: str
    category: ExpenseCategory
    amount_cents: int
    is_tax_deductible: bool
    # The receipt itself is read through GET /expenses/{id}/receipt (a short-lived link);
    # its storage path never leaves the server.
    has_receipt: bool
    receipt_content_type: ReceiptType | None
    receipt_size_bytes: int | None
    receipt_uploaded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExpenseSummary(BaseModel):
    """Totals over every expense matching the filters (all pages)."""

    count: int
    total_cents: int
    deductible_cents: int


class ReceiptUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a path or tenant_id in the body is a 422

    content_type: ReceiptType


class ReceiptUploadTicket(BaseModel):
    upload_id: UUID
    upload_url: str  # PUT the file here with the same Content-Type
    max_bytes: int


class ReceiptRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    content_type: ReceiptType


class ReceiptLink(BaseModel):
    url: str
    expires_in: int  # seconds
    content_type: ReceiptType
