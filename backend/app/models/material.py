"""Material schemas. Mirrors public.materials (Schema §4).

unit_cost is integer cents. Quantities are numeric(12,2) in the database: they are handled as
Decimal with at most 2 decimal places and returned as JSON strings (e.g. "3.00") so they stay
exact. Input accepts a JSON number or a string.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.product import Cents

# numeric(12,2): up to 10 integer digits and 2 decimals. Negative stock is not meaningful.
Quantity = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


class MaterialCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    unit: Annotated[str, Field(min_length=1, max_length=32)]
    unit_cost_cents: Cents
    qty_on_hand: Quantity = Decimal("0")
    reorder_threshold: Quantity = Decimal("0")


class MaterialUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    unit: Annotated[str, Field(min_length=1, max_length=32)] | None = None
    unit_cost_cents: Cents | None = None
    qty_on_hand: Quantity | None = None
    reorder_threshold: Quantity | None = None


# Every materials column is NOT NULL.
NULLABLE_FIELDS: frozenset[str] = frozenset()


class MaterialOut(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    unit: str
    unit_cost_cents: int
    qty_on_hand: Decimal
    reorder_threshold: Decimal
    is_low: bool
    created_at: datetime
    updated_at: datetime
