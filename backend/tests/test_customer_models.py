"""Unit tests for the customer models and the retail/wholesale rule (no database)."""

import pytest
from pydantic import ValidationError

from app.models.customer import CustomerCreate, CustomerUpdate, wholesale_rule_errors

WHOLESALE = {
    "customer_type": "wholesale",
    "first_name": "Elena",
    "company_name": "Bella Boutique",
    "tax_exempt": True,
    "resale_certificate_number": "NJ-1",
    "wholesale_discount_bps": 1500,
    "payment_terms": "net_30",
}


def test_plain_retail_customer_is_valid() -> None:
    c = CustomerCreate(first_name="Sarah", email="sarah@example.com", country="us")
    assert c.customer_type == "retail" and c.country == "US"


def test_complete_wholesale_customer_is_valid() -> None:
    assert CustomerCreate(**WHOLESALE).wholesale_discount_bps == 1500


@pytest.mark.parametrize(
    "field_value",
    [
        {"company_name": "Co"},
        {"tax_exempt": True, "resale_certificate_number": "X-1"},
        {"resale_certificate_number": "X-1"},
        {"wholesale_discount_bps": 500},
        {"payment_terms": "net_15"},
    ],
)
def test_wholesale_only_fields_rejected_on_retail(field_value: dict) -> None:
    with pytest.raises(ValidationError, match="not allowed on a retail customer"):
        CustomerCreate(first_name="Sarah", **field_value)


def test_wholesale_needs_company_name() -> None:
    with pytest.raises(ValidationError, match="needs a company_name"):
        CustomerCreate(**{**WHOLESALE, "company_name": None})


def test_tax_exempt_needs_certificate() -> None:
    with pytest.raises(ValidationError, match="resale_certificate_number"):
        CustomerCreate(**{**WHOLESALE, "resale_certificate_number": None})


@pytest.mark.parametrize(
    "bad",
    [
        {"wholesale_discount_bps": 10001},
        {"wholesale_discount_bps": -1},
        {"payment_terms": "net_90"},
        {"customer_type": "vip"},
        {"email": "not-an-email"},
        {"country": "USA"},
        {"first_name": ""},
        {"tenant_id": "22222222-2222-2222-2222-222222222222"},
    ],
)
def test_invalid_values_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        CustomerCreate(**{**WHOLESALE, **bad})


def test_merged_row_rule_catches_type_switch() -> None:
    current = {**CustomerCreate(**WHOLESALE).model_dump(mode="json")}
    switched = {**current, **CustomerUpdate(customer_type="retail").model_dump(exclude_unset=True)}
    errors = wholesale_rule_errors(switched)
    assert errors and "company_name" in errors[0] and "payment_terms" in errors[0]

    cleared = {
        **switched,
        "company_name": None,
        "tax_exempt": False,
        "resale_certificate_number": None,
        "wholesale_discount_bps": 0,
        "payment_terms": "due_on_receipt",
    }
    assert wholesale_rule_errors(cleared) == []
