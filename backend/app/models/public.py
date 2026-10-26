"""Public storefront schemas (TRD §6.11). Everything here is shown to anonymous shoppers.

These are exact lists: a field is public only if it appears here (and in the storefront view
it is read from). Never public: SKU, stock count, low-stock threshold, costs, the tax ID, the
Stripe account, document counters, customers, invoices, or anything of another tenant.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from app.models.product import MetalType


class Availability(StrEnum):
    in_stock = "in_stock"
    low = "low"  # below the product's low-stock threshold; the count itself is never shown
    out_of_stock = "out_of_stock"
    made_to_order = "made_to_order"  # made after the order; never out of stock


class FontPreset(StrEnum):
    classic = "classic"  # Fraunces + Archivo
    modern = "modern"  # DM Serif Display + Work Sans


class CornerStyle(StrEnum):
    square = "square"
    pill = "pill"


class MarkStyle(StrEnum):
    monogram = "monogram"  # the first letter in a ring, when there is no logo
    wordmark = "wordmark"  # the business name only


class PageSlug(StrEnum):
    returns = "returns"
    privacy = "privacy"
    terms = "terms"
    custom_orders = "custom-orders"


class PageLink(BaseModel):
    slug: PageSlug
    title: str


class HeroButton(BaseModel):
    """The optional second hero button: present only when both label and link are set."""

    label: str
    url: str  # https:// or tel:, checked when saved (API and database)


class PublicStoreOut(BaseModel):
    business_name: str
    tagline: str | None
    primary_color: str
    accent_color: str
    logo_url: str | None
    hero_eyebrow: str | None
    hero_title: str | None
    hero_subtitle: str | None
    collection_title: str | None
    font_preset: FontPreset
    corner_style: CornerStyle
    mark_style: MarkStyle
    hero_button: HeroButton | None
    currency: str
    contact_email: str | None
    pages: list[PageLink]  # pages with text, in footer order
    has_made_to_order: bool  # any published made-to-order product (the "Made for you" note)
    checkout_enabled: bool  # false until checkout ships (Slice A2)


class PublicPageOut(BaseModel):
    slug: PageSlug
    title: str
    body: str  # plain text; the storefront renders it as text, never as HTML


class PublicImageOut(BaseModel):
    url: str  # 1200 px WebP
    thumb_url: str  # 400 px WebP
    width: int
    height: int
    is_primary: bool


class PublicProductOut(BaseModel):
    id: UUID
    name: str
    description: str | None
    metal: MetalType
    price_cents: int  # the regular price
    sale_price_cents: int | None  # only while a sale is active
    sale_ends_at: datetime | None  # only while a sale is active
    unit_price_cents: int  # what the shopper pays now: the sale price if active, else the price
    availability: Availability
    lead_time_note: str | None  # e.g. "Ships in 2-3 weeks"
    images: list[PublicImageOut]  # in display order
