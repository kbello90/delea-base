"""Expense schemas. Mirrors public.expenses (Schema §5); money is integer cents."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.product import Cents

Description = Annotated[str, Field(min_length=1, max_length=500)]
Category = Annotated[str, Field(min_length=1, max_length=100)]
Url = Annotated[str, Field(min_length=1, max_length=2000)]


class ExpenseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_date: date
    description: Description
    category: Category | None = None
    amount_cents: Cents
    is_tax_deductible: bool = True
    receipt_url: Url | None = None


class ExpenseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expense_date: date | None = None
    description: Description | None = None
    category: Category | None = None
    amount_cents: Cents | None = None
    is_tax_deductible: bool | None = None
    receipt_url: Url | None = None


NULLABLE_FIELDS = frozenset({"category", "receipt_url"})


class ExpenseOut(BaseModel):
    id: UUID
    tenant_id: UUID
    expense_date: date
    description: str
    category: str | None
    amount_cents: int
    is_tax_deductible: bool
    receipt_url: str | None
    created_at: datetime
    updated_at: datetime
