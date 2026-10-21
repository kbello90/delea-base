"""Settings through the real app with real tokens. Original rows are restored after each test."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

COMPANY = "/api/v1/settings/company"
BRAND = "/api/v1/settings/brand"
CURRENCY = "/api/v1/settings/currency"

_COMPANY_COLS = (
    "legal_name",
    "ein",
    "address",
    "currency",
    "default_language",
    "enabled_languages",
    "default_tax_rate_bps",
    "stripe_account_id",
    "next_quote_number",
    "next_invoice_number",
)
_BRAND_COLS = (
    "business_name",
    "tagline",
    "primary_color",
    "accent_color",
    "logo_url",
    "hero_title",
    "hero_subtitle",
)


@pytest.fixture(autouse=True)
def restore_settings(db: psycopg.Connection) -> Iterator[None]:
    saved = {}
    for table, cols in (("company_settings", _COMPANY_COLS), ("brand_config", _BRAND_COLS)):
        saved[table] = (
            cols,
            db.execute(
                f"select tenant_id, {', '.join(cols)} from public.{table} "
                "where tenant_id in (%s, %s)",
                (TENANT_A, TENANT_B),
            ).fetchall(),
        )
    yield
    for table, (cols, rows) in saved.items():
        assignments = ", ".join(f"{c} = %s" for c in cols)
        for row in rows:
            db.execute(
                f"update public.{table} set {assignments} where tenant_id = %s", (*row[1:], row[0])
            )


def test_company_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    # Owner only; each owner sees and edits only their own tenant's row.
    assert_access(client, users, "GET", COMPANY, {"owner_a": 200, "owner_b": 200})
    assert_access(
        client,
        users,
        "PATCH",
        COMPANY,
        {"owner_a": 200, "owner_b": 200},
        json={"legal_name": "IT Legal Name"},
    )


def test_brand_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    assert_access(client, users, "GET", BRAND, {"owner_a": 200, "staff_a": 200, "owner_b": 200})
    assert_access(
        client,
        users,
        "PATCH",
        BRAND,
        {"owner_a": 200, "owner_b": 200},
        json={"tagline": "IT tagline"},
    )


def test_company_get_and_patch(client: TestClient, users: dict[str, TestUser], db) -> None:
    owner = users["owner_a"]
    current = client.get(COMPANY, headers=owner.headers).json()
    assert current["tenant_id"] == TENANT_A
    assert (current["legal_name"], current["ein"], current["default_tax_rate_bps"]) == (
        "DELEA Jewelry LLC",
        "12-3456789",
        800,
    )

    updated = client.patch(
        COMPANY,
        json={
            "currency": "eur",
            "default_tax_rate_bps": 0,
            "default_language": "fr",
            "enabled_languages": ["fr", "en"],
        },
        headers=owner.headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["currency"], body["default_tax_rate_bps"], body["default_language"]) == (
        "EUR",
        0,
        "fr",
    )
    # Tenant B untouched.
    b = db.execute(
        "select currency, default_tax_rate_bps from public.company_settings where tenant_id = %s",
        (TENANT_B,),
    ).fetchone()
    assert b == ("USD", 550)


@pytest.mark.parametrize(
    "bad",
    [
        {"default_tax_rate_bps": 10001},
        {"default_tax_rate_bps": -1},
        {"currency": "DOLLARS"},
        {"currency": "JPY"},  # valid ISO code, but not a two-decimal currency
        {"currency": "KWD"},
        {"default_language": "de"},
        {"enabled_languages": ["en", "pt"]},
        # Merged-row rule: tenant A's default is 'en', so it must stay enabled.
        {"enabled_languages": ["es"]},
    ],
)
def test_company_validation(client: TestClient, users: dict[str, TestUser], bad: dict) -> None:
    response = client.patch(COMPANY, json=bad, headers=users["owner_a"].headers)
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(
    "field", ["stripe_account_id", "tenant_id", "next_quote_number", "next_invoice_number"]
)
def test_read_only_company_fields(
    client: TestClient, users: dict[str, TestUser], db, field: str
) -> None:
    value = TENANT_B if field == "tenant_id" else ("acct_IT" if field == "stripe_account_id" else 1)
    response = client.patch(COMPANY, json={field: value}, headers=users["owner_a"].headers)
    assert response.status_code == 422
    row = db.execute(
        "select stripe_account_id, next_quote_number, next_invoice_number "
        "from public.company_settings where tenant_id = %s",
        (TENANT_A,),
    ).fetchone()
    assert row == (None, 43, 90)


def test_brand_get_and_patch(client: TestClient, users: dict[str, TestUser]) -> None:
    staff_view = client.get(BRAND, headers=users["staff_a"].headers).json()
    assert (staff_view["business_name"], staff_view["primary_color"]) == ("DELEA", "#6B2737")

    owner = users["owner_a"]
    updated = client.patch(
        BRAND,
        json={"primary_color": "#1f3a5f", "hero_title": None, "business_name": "IT Brand"},
        headers=owner.headers,
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["primary_color"], updated.json()["hero_title"]) == ("#1F3A5F", None)
    assert (
        client.get(BRAND, headers=users["owner_b"].headers).json()["business_name"] == "Aurora Gems"
    )

    for bad in ({"accent_color": "blue"}, {"business_name": None}, {"tenant_id": TENANT_B}, {}):
        assert client.patch(BRAND, json=bad, headers=owner.headers).status_code == 422, bad


def test_staff_and_cpa_cannot_write_brand(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    for name in ("staff_a", "cpa_a"):
        response = client.patch(BRAND, json={"tagline": "hacked"}, headers=users[name].headers)
        assert response.status_code == 403
    assert (
        db.execute(
            "select tagline from public.brand_config where tenant_id = %s", (TENANT_A,)
        ).fetchone()[0]
        == "Jewelry Operations"
    )


def test_currency_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    # Every member role of both tenants; the user without a membership gets 403.
    assert_access(
        client,
        users,
        "GET",
        CURRENCY,
        {"owner_a": 200, "staff_a": 200, "cpa_a": 200, "owner_b": 200},
    )


def test_currency_is_per_tenant(client: TestClient, users: dict[str, TestUser], db) -> None:
    db.execute(
        "update public.company_settings set currency = 'CAD' where tenant_id = %s", (TENANT_B,)
    )
    try:
        got = {
            name: client.get(CURRENCY, headers=users[name].headers).json()
            for name in ("owner_a", "staff_a", "cpa_a", "owner_b")
        }
        assert got == {
            "owner_a": {"currency": "USD"},
            "staff_a": {"currency": "USD"},
            "cpa_a": {"currency": "USD"},
            "owner_b": {"currency": "CAD"},
        }
        refused = client.get(CURRENCY, headers=users["no_membership"].headers)
        assert refused.status_code == 403
        assert refused.json() == {"detail": "No tenant membership"}
    finally:
        db.execute(
            "update public.company_settings set currency = 'USD' where tenant_id = %s",
            (TENANT_B,),
        )
