"""Product schemas. Mirrors public.products (Schema §4); money is integer cents.

Images are managed only through the image endpoints (product_images table); there is no
writable image field on a product.

Request models forbid unknown fields, so a client-sent `tenant_id` is rejected (422) and
can never reach the database. The tenant always comes from the verified token.
"""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

BIGINT_MAX = 2**63 - 1
INT_MAX = 2**31 - 1

Cents = Annotated[int, Field(ge=0, le=BIGINT_MAX)]
Int32 = Annotated[int, Field(ge=-(2**31), le=INT_MAX)]
# Shown on the storefront next to a made-to-order product, e.g. "Ships in 2-3 weeks".
LeadTimeNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


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
    icon: str | None = None
    is_published: bool = False
    sort_order: Int32 = 0
    made_to_order: bool = False  # made after the sale: never out of stock, stock not decremented
    lead_time_note: LeadTimeNote | None = None


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
    icon: str | None = None
    is_published: bool | None = None
    sort_order: Int32 | None = None
    made_to_order: bool | None = None
    lead_time_note: LeadTimeNote | None = None


# Columns that may be null in the database; for every other PATCH field an explicit null
# is rejected rather than passed through as a not-null violation.
NULLABLE_FIELDS = frozenset(
    {"description", "sale_price_cents", "sale_ends_at", "icon", "lead_time_note"}
)


class ProductImageOut(BaseModel):
    """One image, ordered by position. URLs are the public base URL plus the stored path."""

    id: UUID
    position: int
    is_primary: bool
    url: str  # 1200 px WebP
    thumb_url: str  # 400 px WebP
    width: int
    height: int


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
    icon: str | None
    is_published: bool
    sort_order: int
    made_to_order: bool
    lead_time_note: str | None
    created_at: datetime
    updated_at: datetime
    images: list[ProductImageOut]
    personalization: list["PersonalizationFieldOut"]


# ---------------------------------------------------------------------------
# Personalization fields (Slice A2): up to five per product, answered by shoppers.
# ---------------------------------------------------------------------------
MAX_PERSONALIZATION_FIELDS = 5
FieldLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class PersonalizationFieldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Keep an existing field's id when editing it: shoppers' carts refer to fields by id.
    id: UUID | None = None
    label: FieldLabel
    kind: Literal["text", "choice"]
    required: bool = False
    max_length: Annotated[int, Field(ge=1, le=200)] | None = None  # text: default 40
    choices: Annotated[list[FieldLabel], Field(min_length=1, max_length=30)] | None = None

    @model_validator(mode="after")
    def _kind_rules(self) -> "PersonalizationFieldIn":
        if self.kind == "text":
            if self.choices is not None:
                raise ValueError("a text field has no choices")
            if self.max_length is None:
                self.max_length = 40
        else:
            if not self.choices:
                raise ValueError("a choice field needs at least one choice")
            if self.max_length is not None:
                raise ValueError("a choice field has no maximum length")
            if len({c.lower() for c in self.choices}) != len(self.choices):
                raise ValueError("choices must not repeat")
        return self


class PersonalizationReplace(BaseModel):
    """PUT body: the whole list, in display order. An empty list removes every field."""

    model_config = ConfigDict(extra="forbid")

    fields: Annotated[list[PersonalizationFieldIn], Field(max_length=MAX_PERSONALIZATION_FIELDS)]

    @model_validator(mode="after")
    def _unique(self) -> "PersonalizationReplace":
        labels = [f.label.lower() for f in self.fields]
        if len(set(labels)) != len(labels):
            raise ValueError("labels must not repeat")
        ids = [f.id for f in self.fields if f.id is not None]
        if len(set(ids)) != len(ids):
            raise ValueError("a field id can appear once")
        return self


class PersonalizationFieldOut(BaseModel):
    id: UUID
    position: int
    label: str
    kind: Literal["text", "choice"]
    required: bool
    max_length: int | None
    choices: list[str] | None
