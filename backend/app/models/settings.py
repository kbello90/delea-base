"""Company settings and brand config schemas (Schema §3).

Not writable here: tenant identity (tenant_id), the currency (set at provisioning; the
database refuses a change once the tenant has data), stripe_account_id (set by the Stripe
Connect flow), the document counters next_quote_number / next_invoice_number (rewriting them
could reuse document numbers), and the logo (uploaded through its own endpoints). Request
models forbid unknown fields, so sending any of these is a 422.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints


class Language(StrEnum):
    """Storefront languages (company_settings.default_language check: en / es / fr)."""

    en = "en"
    es = "es"
    fr = "fr"


# v1 stores every amount as integer hundredths of the major unit ("cents"), so only currencies
# with exactly two decimal places are supported (no JPY, no three-decimal currencies).
SUPPORTED_CURRENCIES = ("USD", "CAD", "EUR", "GBP", "MXN", "AUD", "NZD", "CHF")


def _hex_color(value: str) -> str:
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        raise ValueError("must be a hex color like #6B2737")
    return value.upper()


def _distinct_languages(value: list[Language]) -> list[Language]:
    if len(set(value)) != len(value):
        raise ValueError("languages must not repeat")
    return value


# The second hero button's link: https:// with a plain host (https://wa.me/... included) or a
# tel: number. The database checks the same (brand_config_hero_cta_url).
_HTTPS_LINK = re.compile(
    r"https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+(:[0-9]{1,5})?"
    r"([/?#][^\s<>\"]*)?",
    re.IGNORECASE,
)
_TEL_LINK = re.compile(r"tel:\+?[0-9][0-9().-]{2,29}")
# The storefront_settings_contact_email check.
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _hero_link(value: str) -> str:
    if len(value) > 500 or not (_HTTPS_LINK.fullmatch(value) or _TEL_LINK.fullmatch(value)):
        raise ValueError(
            "must be an https:// link (for example https://wa.me/15550100142) or a tel: number"
        )
    return value


def _email(value: str) -> str:
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise ValueError("must be an email address")
    return value


HexColor = Annotated[str, AfterValidator(_hex_color)]
TaxRateBps = Annotated[int, Field(ge=0, le=10000)]
Languages = Annotated[list[Language], Field(min_length=1), AfterValidator(_distinct_languages)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=2000)]


def _trimmed(max_length: int) -> Any:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


HeroLink = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_hero_link)]
Email = Annotated[str, StringConstraints(strip_whitespace=True), AfterValidator(_email)]
PageText = Annotated[str, _trimmed(20000)]


class FontPreset(StrEnum):
    classic = "classic"  # Fraunces + Archivo
    modern = "modern"  # DM Serif Display + Work Sans


class CornerStyle(StrEnum):
    square = "square"
    pill = "pill"


class MarkStyle(StrEnum):
    monogram = "monogram"  # the first letter in a ring, when there is no logo
    wordmark = "wordmark"  # the business name only


def language_rule_errors(row: dict[str, Any]) -> list[str]:
    """The default language must be one of the enabled languages (checked on the merged row)."""
    if row["default_language"] not in row["enabled_languages"]:
        return ["default_language must be one of enabled_languages"]
    return []


class CompanySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: Text | None = None
    ein: Annotated[str, Field(min_length=1, max_length=50)] | None = None  # any country's tax ID
    address: LongText | None = None
    default_language: Language | None = None
    enabled_languages: Languages | None = None
    default_tax_rate_bps: TaxRateBps | None = None


COMPANY_NULLABLE = frozenset({"legal_name", "ein", "address"})


class CompanySettingsOut(BaseModel):
    tenant_id: UUID
    legal_name: str | None
    ein: str | None
    address: str | None
    currency: str
    default_language: Language
    enabled_languages: list[str]
    default_tax_rate_bps: int
    stripe_account_id: str | None  # read-only here
    next_quote_number: int  # read-only
    next_invoice_number: int  # read-only
    updated_at: datetime


class BrandConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: Text | None = None
    tagline: Text | None = None
    primary_color: HexColor | None = None
    accent_color: HexColor | None = None
    hero_title: Text | None = None
    hero_subtitle: LongText | None = None
    hero_eyebrow: Annotated[str, _trimmed(60)] | None = None
    collection_title: Annotated[str, _trimmed(80)] | None = None
    font_preset: FontPreset | None = None
    corner_style: CornerStyle | None = None
    mark_style: MarkStyle | None = None
    # The optional second hero button; the storefront shows it only when both are set.
    hero_cta_label: Annotated[str, _trimmed(40)] | None = None
    hero_cta_url: HeroLink | None = None


BRAND_NULLABLE = frozenset(
    {
        "tagline",
        "hero_title",
        "hero_subtitle",
        "hero_eyebrow",
        "collection_title",
        "hero_cta_label",
        "hero_cta_url",
    }
)


class BrandConfigOut(BaseModel):
    tenant_id: UUID
    business_name: str
    tagline: str | None
    primary_color: str
    accent_color: str
    logo_url: str | None  # the public URL of the uploaded logo (brand-assets bucket)
    hero_title: str | None
    hero_subtitle: str | None
    hero_eyebrow: str | None
    collection_title: str | None
    font_preset: FontPreset
    corner_style: CornerStyle
    mark_style: MarkStyle
    hero_cta_label: str | None
    hero_cta_url: str | None
    updated_at: datetime


class StorefrontSettingsUpdate(BaseModel):
    """PATCH /settings/storefront. Everything here is shown on the public storefront."""

    model_config = ConfigDict(extra="forbid")

    storefront_enabled: bool | None = None
    contact_email: Email | None = None
    return_policy: PageText | None = None
    privacy_policy: PageText | None = None
    terms: PageText | None = None
    custom_orders_info: PageText | None = None  # the "How custom orders work" page


STOREFRONT_NULLABLE = frozenset(
    {"contact_email", "return_policy", "privacy_policy", "terms", "custom_orders_info"}
)


class StorefrontSettingsOut(BaseModel):
    storefront_enabled: bool
    contact_email: str | None
    return_policy: str | None
    privacy_policy: str | None
    terms: str | None
    custom_orders_info: str | None
    updated_at: datetime


class CurrencyOut(BaseModel):
    """The tenant's currency code; every amount is hundredths of its major unit."""

    currency: str


class LogoType(StrEnum):
    jpeg = "image/jpeg"
    png = "image/png"
    webp = "image/webp"


LOGO_EXTENSIONS = {LogoType.jpeg: "jpg", LogoType.png: "png", LogoType.webp: "webp"}


class LogoUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a path or tenant_id in the body is a 422

    content_type: LogoType


class LogoUploadTicket(BaseModel):
    upload_id: UUID
    upload_url: str  # PUT the file here with the same Content-Type
    max_bytes: int


class LogoRegister(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    content_type: LogoType
