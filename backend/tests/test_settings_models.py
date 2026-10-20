"""Unit tests for the settings models (no database)."""

import pytest
from pydantic import ValidationError

from app.models.settings import BrandConfigUpdate, CompanySettingsUpdate, language_rule_errors


def test_valid_company_update_normalizes_currency() -> None:
    u = CompanySettingsUpdate(
        currency="eur", default_tax_rate_bps=0, enabled_languages=["en", "fr"]
    )
    assert u.currency == "EUR"


@pytest.mark.parametrize(
    "bad",
    [
        {"default_tax_rate_bps": -1},
        {"default_tax_rate_bps": 10001},
        {"default_tax_rate_bps": 8.5},
        {"currency": "US"},
        {"currency": "US$"},
        {"default_language": "de"},
        {"enabled_languages": []},
        {"enabled_languages": ["en", "en"]},
        {"enabled_languages": ["en", "pt"]},
        {"stripe_account_id": "acct_123"},
        {"tenant_id": "22222222-2222-2222-2222-222222222222"},
        {"next_invoice_number": 1},
        {"next_quote_number": 1},
    ],
)
def test_invalid_company_updates_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        CompanySettingsUpdate(**bad)


@pytest.mark.parametrize("bps", [0, 1, 800, 10000])
def test_tax_rate_bounds_inclusive(bps: int) -> None:
    assert CompanySettingsUpdate(default_tax_rate_bps=bps).default_tax_rate_bps == bps


def test_language_rule() -> None:
    assert language_rule_errors({"default_language": "es", "enabled_languages": ["en", "es"]}) == []
    assert language_rule_errors({"default_language": "fr", "enabled_languages": ["en"]})


def test_brand_colors_validated_and_uppercased() -> None:
    assert BrandConfigUpdate(primary_color="#6b2737").primary_color == "#6B2737"
    for bad in ("6B2737", "#6B273", "#6B27377", "#GGGGGG", "red", "#fff"):
        with pytest.raises(ValidationError):
            BrandConfigUpdate(accent_color=bad)


def test_brand_identity_not_writable() -> None:
    with pytest.raises(ValidationError):
        BrandConfigUpdate(tenant_id="11111111-1111-1111-1111-111111111111")
