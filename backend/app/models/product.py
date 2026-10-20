"""Product schemas. Mirrors public.products (Schema §4); money is integer cents.

Request models forbid unknown fields, so a client-sent `tenant_id` is rejected (422) and
can never reach the database. The tenant always comes from the verified token.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

BIGINT_MAX = 2**63 - 1
INT_MAX = 2**31 - 1

Cents = Annotated[int, Field(ge=0, le=BIGINT_MAX)]
Int32 = Annotated[int, Field(ge=-(2**31), le=INT_MAX)]


class MetalType(StrEnum):
    """Matches the `metal_type` Postgres enum."""

    gold_14k = "gold_14k"
    gold_18k = "gold_18k"
    rose_gold = "rose_gold"
    white_gold = "white_gold"
    silver_925 = "silver_925"
    platinum = "platinum"
    other = "other"


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, Field(min_length=1, max_length=64)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None
    metal: MetalType
    price_cents: Cents
    sale_price_cents: Cents | None = None
    sale_ends_at: datetime | None = None
    stock_qty: Int32 = 0
    low_stock_threshold: Int32 = 3
    image_urls: list[str] = Field(default_factory=list)
    icon: str | None = None
    is_published: bool = False
    sort_order: Int32 = 0


class ProductUpdate(BaseModel):
    """PATCH body: only the fields sent are changed."""

    model_config = ConfigDict(extra="forbid")

    sku: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: str | None = None
    metal: MetalType | None = None
    price_cents: Cents | None = None
    sale_price_cents: Cents | None = None
    sale_ends_at: datetime | None = None
    stock_qty: Int32 | None = None
    low_stock_threshold: Int32 | None = None
    image_urls: list[str] | None = None
    icon: str | None = None
    is_published: bool | None = None
    sort_order: Int32 | None = None


# Columns that may be null in the database; for every other PATCH field an explicit null
# is rejected rather than passed through as a not-null violation.
NULLABLE_FIELDS = frozenset({"description", "sale_price_cents", "sale_ends_at", "icon"})


class ProductOut(BaseModel):
    id: UUID
    tenant_id: UUID
    sku: str
    name: str
    description: str | None
    metal: MetalType
    price_cents: int
    sale_price_cents: int | None
    sale_ends_at: datetime | None
    stock_qty: int
    low_stock_threshold: int
    image_urls: list[str]
    icon: str | None
    is_published: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime
