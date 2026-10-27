"""Public storefront schemas (TRD §6.11). Everything here is shown to anonymous shoppers.

These are exact lists: a field is public only if it appears here (and in the storefront view
it is read from). Never public: SKU, stock count, low-stock threshold, costs, the tax ID, the
Stripe account, document counters, customers, invoices, or anything of another tenant.

Request models forbid unknown fields, so a price, total, tenant or status sent by a client is
a 422. Every amount comes from the database's pricing function (storefront_price_cart).
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.models.product import MetalType
from app.models.settings import CornerStyle, FontPreset, MarkStyle


class Availability(StrEnum):
    in_stock = "in_stock"
    low = "low"  # below the product's low-stock threshold; the count itself is never shown
    out_of_stock = "out_of_stock"
    made_to_order = "made_to_order"  # made after the order; never out of stock


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


class ShippingOption(BaseModel):
    flat_cents: int  # charged per order
    free_over_cents: int | None  # free when the subtotal reaches this; None: never free


class PickupOption(BaseModel):
    note: str | None  # where and when


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
    checkout_enabled: bool  # orders can be placed: a way to receive, a way to pay, protected
    shipping: ShippingOption | None  # None: the shop doesn't ship
    pickup: PickupOption | None  # None: no local pickup
    payment_choices: list[Literal["bank_transfer", "pay_on_pickup"]]  # pay_on_pickup: pickup only
    tax_rate_bps: int  # the shop's single rate (TRD §6.11, accepted limitation)
    tax_shipping: bool


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


class PersonalizationFieldOut(BaseModel):
    id: UUID  # the key for the shopper's answer
    label: str
    kind: Literal["text", "choice"]
    required: bool
    max_length: int | None  # text only
    choices: list[str] | None  # choice only


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
    personalization: list[PersonalizationFieldOut]  # in display order


# ---------------------------------------------------------------------------
# Checkout (Slice A2)
# ---------------------------------------------------------------------------
class Answer(BaseModel):
    label: str
    value: str


Fulfillment = Literal["ship", "pickup"]
PaymentChoice = Literal["bank_transfer", "pay_on_pickup"]
AnswerText = Annotated[str, Field(max_length=200)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class CartItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    qty: Annotated[int, Field(ge=1, le=99)]
    # field id -> answer; checked against the product's fields by the database
    personalization: Annotated[dict[UUID, AnswerText], Field(max_length=5)] = {}


CartItems = Annotated[list[CartItemIn], Field(min_length=1, max_length=50)]


class QuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: CartItems
    fulfillment: Fulfillment | None = None  # not chosen yet: no shipping in the total


class QuoteLineOut(BaseModel):
    product_id: UUID
    name: str | None  # None: the product isn't available
    qty: int
    unit_price_cents: int | None
    line_total_cents: int | None
    personalization: list[Answer] | None
    made_to_order: bool
    lead_time_note: str | None
    problem: Literal["unavailable", "options"] | None


class QuoteOut(BaseModel):
    lines: list[QuoteLineOut]
    subtotal_cents: int
    shipping_cents: int | None  # None: delivery not chosen yet
    tax_rate_bps: int
    tax_cents: int
    total_cents: int
    has_problems: bool


def _email(value: str) -> str:
    if len(value) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("must be an email address")
    return value


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: Name
    last_name: Name
    email: Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_email)]
    phone: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None
    ) = None


US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split()
)


def _state(value: str) -> str:
    value = value.upper()
    if value not in US_STATES:
        raise ValueError("must be a US state code like ME")
    return value


class ShipToIn(BaseModel):
    """A US address (v1 ships within the US only)."""

    model_config = ConfigDict(extra="forbid")

    line1: Line
    line2: Line | None = None
    city: Name
    state: Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_state)]
    postal_code: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{5}(-[0-9]{4})?$")
    ]


# Faster than this from loading the checkout page to placing the order is a bot.
MIN_FILL_MILLISECONDS = 2000


class OrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkout_key: UUID  # made by the browser when the checkout opens; a resend reuses it
    items: CartItems
    fulfillment: Fulfillment
    payment_choice: PaymentChoice
    expected_total_cents: Annotated[int, Field(ge=0)]  # what the shopper saw; never charged
    contact: ContactIn
    ship_to: ShipToIn | None = None
    note: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
        | None
    ) = None
    turnstile_token: Annotated[str, Field(max_length=2048)] | None = None
    website: str | None = None  # a field people never see: anything in it is a bot
    elapsed_ms: Annotated[int, Field(ge=0)]  # time since the checkout page loaded

    @model_validator(mode="after")
    def _consistent(self) -> "OrderRequest":
        if self.fulfillment == "ship" and self.ship_to is None:
            raise ValueError("a shipping address is required")
        if self.fulfillment == "pickup" and self.ship_to is not None:
            raise ValueError("pickup orders have no shipping address")
        if self.payment_choice == "pay_on_pickup" and self.fulfillment != "pickup":
            raise ValueError("paying on pickup needs pickup")
        return self

    def order_document(self) -> dict:
        """What the database receives, and what the idempotency fingerprint covers: the order
        itself, without the bot checks or the key."""
        return self.model_dump(
            mode="json",
            exclude={"checkout_key", "turnstile_token", "website", "elapsed_ms"},
            exclude_none=True,
        )


# deposit_paid: part paid, still owing (Slice B1). in_production also covers an order whose
# production started on a deposit, before the rest is paid.
ShopperStatus = Literal[
    "awaiting_payment",
    "deposit_paid",
    "paid",
    "in_production",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
]


class TrackedLineOut(BaseModel):
    description: str
    qty: int
    unit_price_cents: int
    line_total_cents: int
    personalization: list[Answer] | None
    lead_time_note: str | None


class OrderOut(BaseModel):
    """The order as the shopper sees it: right after placing it, and on the tracking page."""

    order_ref: str
    public_token: UUID  # the tracking link's secret part
    placed_at: datetime
    status: ShopperStatus
    fulfillment: Fulfillment
    payment_choice: PaymentChoice
    lines: list[TrackedLineOut]
    subtotal_cents: int
    shipping_cents: int
    tax_rate_bps: int
    tax_cents: int
    total_cents: int
    first_name: str
    ship_city: str | None  # city and state only: the full address is never shown here
    ship_state: str | None
    payment_instructions: str | None  # while unpaid
    pickup_note: str | None
    amount_paid_cents: int  # received so far (a deposit, or everything)
    balance_cents: int  # still due; 0 once paid in full or cancelled
