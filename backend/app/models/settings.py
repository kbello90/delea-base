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

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


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


HexColor = Annotated[str, AfterValidator(_hex_color)]
TaxRateBps = Annotated[int, Field(ge=0, le=10000)]
Languages = Annotated[list[Language], Field(min_length=1), AfterValidator(_distinct_languages)]
Text = Annotated[str, Field(min_length=1, max_length=200)]
LongText = Annotated[str, Field(min_length=1, max_length=2000)]


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


BRAND_NULLABLE = frozenset({"tagline", "hero_title", "hero_subtitle"})


class BrandConfigOut(BaseModel):
    tenant_id: UUID
    business_name: str
    tagline: str | None
    primary_color: str
    accent_color: str
    logo_url: str | None  # the public URL of the uploaded logo (brand-assets bucket)
    hero_title: str | None
    hero_subtitle: str | None
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
