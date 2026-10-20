"""Customer schemas. Mirrors public.customers (Schema §5).

Retail vs wholesale: the wholesale-only fields (company name, tax-exempt flag, resale
certificate number, wholesale discount, payment terms) must stay at their defaults on a retail
customer. `wholesale_rule_errors` is the single place that rule lives; it is applied to the
create body and to the merged row on PATCH. The database's check constraints are the backstop.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class CustomerType(StrEnum):
    retail = "retail"
    wholesale = "wholesale"


class PaymentTerms(StrEnum):
    due_on_receipt = "due_on_receipt"
    net_15 = "net_15"
    net_30 = "net_30"
    net_60 = "net_60"


_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _email(value: str) -> str:
    if not _EMAIL.match(value):
        raise ValueError("must be an email address")
    return value


def _country(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{2}", value):
        raise ValueError("must be a 2-letter country code")
    return value.upper()


Text = Annotated[str, Field(min_length=1, max_length=200)]
Email = Annotated[str, Field(max_length=254), AfterValidator(_email)]
Country = Annotated[str, AfterValidator(_country)]
DiscountBps = Annotated[int, Field(ge=0, le=10000)]

# Wholesale-only fields and the value each must keep on a retail customer.
RETAIL_DEFAULTS: dict[str, Any] = {
    "company_name": None,
    "tax_exempt": False,
    "resale_certificate_number": None,
    "wholesale_discount_bps": 0,
    "payment_terms": PaymentTerms.due_on_receipt,
}


def wholesale_rule_errors(row: dict[str, Any]) -> list[str]:
    """Problems with the retail/wholesale fields of a complete customer row."""
    errors = []
    if row["customer_type"] == CustomerType.retail:
        set_fields = [f for f, default in RETAIL_DEFAULTS.items() if row.get(f) != default]
        if set_fields:
            errors.append(
                "wholesale-only fields are not allowed on a retail customer: "
                + ", ".join(sorted(set_fields))
            )
    else:
        if not row.get("company_name"):
            errors.append("a wholesale customer needs a company_name")
    if row.get("tax_exempt") and not row.get("resale_certificate_number"):
        errors.append("a tax-exempt customer needs a resale_certificate_number")
    return errors


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_type: CustomerType = CustomerType.retail
    first_name: Text
    last_name: Text | None = None
    email: Email | None = None
    phone: Annotated[str, Field(max_length=50)] | None = None
    address_line1: Text | None = None
    address_line2: Text | None = None
    city: Text | None = None
    state: Annotated[str, Field(max_length=100)] | None = None
    postal_code: Annotated[str, Field(max_length=20)] | None = None
    country: Country = "US"
    marketing_opt_in: bool = False
    notes: Annotated[str, Field(max_length=5000)] | None = None
    company_name: Text | None = None
    tax_exempt: bool = False
    resale_certificate_number: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    wholesale_discount_bps: DiscountBps = 0
    payment_terms: PaymentTerms = PaymentTerms.due_on_receipt

    @model_validator(mode="after")
    def _wholesale_rules(self) -> "CustomerCreate":
        errors = wholesale_rule_errors(self.model_dump())
        if errors:
            raise ValueError("; ".join(errors))
        return self


class CustomerUpdate(BaseModel):
    """PATCH body. The retail/wholesale rule is checked against the merged row."""

    model_config = ConfigDict(extra="forbid")

    customer_type: CustomerType | None = None
    first_name: Text | None = None
    last_name: Text | None = None
    email: Email | None = None
    phone: Annotated[str, Field(max_length=50)] | None = None
    address_line1: Text | None = None
    address_line2: Text | None = None
    city: Text | None = None
    state: Annotated[str, Field(max_length=100)] | None = None
    postal_code: Annotated[str, Field(max_length=20)] | None = None
    country: Country | None = None
    marketing_opt_in: bool | None = None
    notes: Annotated[str, Field(max_length=5000)] | None = None
    company_name: Text | None = None
    tax_exempt: bool | None = None
    resale_certificate_number: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    wholesale_discount_bps: DiscountBps | None = None
    payment_terms: PaymentTerms | None = None


NULLABLE_FIELDS = frozenset(
    {
        "last_name",
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "notes",
        "company_name",
        "resale_certificate_number",
    }
)


class CustomerOut(BaseModel):
    id: UUID
    tenant_id: UUID
    customer_type: CustomerType
    first_name: str
    last_name: str | None
    email: str | None
    phone: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str
    marketing_opt_in: bool
    notes: str | None
    company_name: str | None
    tax_exempt: bool
    resale_certificate_number: str | None
    wholesale_discount_bps: int
    payment_terms: PaymentTerms
    created_at: datetime
    updated_at: datetime
