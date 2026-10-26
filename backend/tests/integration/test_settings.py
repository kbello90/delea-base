"""Settings through the real app with real tokens. Original rows are restored after each test."""

import csv
import io
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access, force_currency

pytestmark = pytest.mark.integration

INVOICES = "/api/v1/invoices"
COMPANY = "/api/v1/settings/company"
BRAND = "/api/v1/settings/brand"
CURRENCY = "/api/v1/settings/currency"
STOREFRONT = "/api/v1/settings/storefront"

_COMPANY_COLS = (
    "legal_name",
    "ein",
    "address",
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
    "logo_path",
    "hero_title",
    "hero_subtitle",
    "hero_eyebrow",
    "collection_title",
    "font_preset",
    "corner_style",
    "mark_style",
    "hero_cta_label",
    "hero_cta_url",
)
_STOREFRONT_COLS = (
    "storefront_enabled",
    "contact_email",
    "return_policy",
    "privacy_policy",
    "terms",
    "custom_orders_info",
)


@pytest.fixture(autouse=True)
def restore_settings(db: psycopg.Connection) -> Iterator[None]:
    saved = {}
    for table, cols in (
        ("company_settings", _COMPANY_COLS),
        ("brand_config", _BRAND_COLS),
        ("storefront_settings", _STOREFRONT_COLS),
    ):
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
            "default_tax_rate_bps": 0,
            "default_language": "fr",
            "enabled_languages": ["fr", "en"],
            "ein": "20100070970",  # any country's tax ID (here a Peruvian RUC)
        },
        headers=owner.headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["currency"], body["default_tax_rate_bps"], body["default_language"]) == (
        "USD",
        0,
        "fr",
    )
    assert body["ein"] == "20100070970"
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
        # The currency is never accepted here, valid code or not (set at provisioning).
        {"currency": "EUR"},
        {"currency": "USD"},
        {"currency": "JPY"},
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

    for bad in (
        {"accent_color": "blue"},
        {"business_name": None},
        {"tenant_id": TENANT_B},
        {},
        {"logo_url": "https://evil.example/logo.png"},
        {"logo_path": f"{TENANT_A}/logo-00000000-0000-4000-8000-000000000000.webp"},
    ):
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
    force_currency(db, TENANT_B, "CAD")
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
        force_currency(db, TENANT_B, "USD")


# ---------------------------------------------------------------------------
# A new default tax rate applies to new quotes only (the fixture restores it)
# ---------------------------------------------------------------------------
def test_a_tax_rate_change_never_rewrites_existing_invoices(client, users, db) -> None:
    owner = users["owner_a"]
    tag = uuid.uuid4().hex[:6].upper()
    product = str(
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty) "
            "values (%s, %s, 'IT rate ring', 'other', 10000, 50) returning id",
            (TENANT_A, f"IT-{tag}"),
        ).fetchone()[0]
    )
    customer = str(
        db.execute(
            "insert into public.customers (tenant_id, first_name, last_name) "
            "values (%s, %s, 'Rate') returning id",
            (TENANT_A, f"IT-{tag}"),
        ).fetchone()[0]
    )
    line = [{"product_id": product, "qty": 1}]

    def create(**extra) -> dict:
        r = client.post(
            INVOICES, json={"customer_id": customer, "items": line, **extra}, headers=owner.headers
        )
        assert r.status_code == 201, r.text
        return r.json()

    quote = create()
    paid = create(pay_now=True, payment_method="card")
    assert (quote["tax_rate_bps"], quote["tax_cents"], quote["total_cents"]) == (800, 800, 10800)
    snapshot = {
        i["id"]: client.get(f"{INVOICES}/{i['id']}", headers=owner.headers).json()
        for i in (quote, paid)
    }

    changed = client.patch(COMPANY, json={"default_tax_rate_bps": 500}, headers=owner.headers)
    assert changed.status_code == 200 and changed.json()["default_tax_rate_bps"] == 500

    # Existing invoices are unchanged, field for field.
    for invoice_id, before in snapshot.items():
        after = client.get(f"{INVOICES}/{invoice_id}", headers=owner.headers).json()
        assert after == before
    # Editing the old quote's lines keeps its own rate (preview and save).
    two = [{"product_id": product, "qty": 2}]
    preview = client.post(
        f"{INVOICES}/{quote['id']}/items/preview", json={"items": two}, headers=owner.headers
    ).json()
    assert (preview["tax_rate_bps"], preview["tax_cents"]) == (800, 1600)
    replaced = client.put(
        f"{INVOICES}/{quote['id']}/items", json={"items": two}, headers=owner.headers
    ).json()
    assert (replaced["tax_rate_bps"], replaced["tax_cents"], replaced["total_cents"]) == (
        800,
        1600,
        21600,
    )
    # A new quote, and its preview, use the new rate.
    new_preview = client.post(
        f"{INVOICES}/preview", json={"customer_id": customer, "items": line}, headers=owner.headers
    ).json()
    assert (new_preview["tax_rate_bps"], new_preview["tax_cents"]) == (500, 500)
    new = create()
    assert (new["tax_rate_bps"], new["tax_cents"], new["total_cents"]) == (500, 500, 10500)
    # The CPA's export shows each invoice's own rate.
    rows = list(
        csv.DictReader(
            io.StringIO(
                client.get(
                    f"{INVOICES}/export.csv",
                    params={"customer_id": customer},
                    headers=users["cpa_a"].headers,
                ).text
            )
        )
    )
    assert sorted(r["tax_rate_bps"] for r in rows) == ["500", "800", "800"]


# ---------------------------------------------------------------------------
# Storefront look (brand) and storefront settings (migration 20261027000100)
# ---------------------------------------------------------------------------
def test_the_storefront_look_is_part_of_the_brand(client: TestClient, users) -> None:
    owner = users["owner_a"].headers
    brand = client.get(BRAND, headers=owner).json()
    assert (brand["font_preset"], brand["corner_style"], brand["mark_style"]) == (
        "classic",
        "square",
        "monogram",
    )
    updated = client.patch(
        BRAND,
        json={
            "hero_eyebrow": " Made to order ",
            "collection_title": "The collection",
            "font_preset": "modern",
            "corner_style": "pill",
            "mark_style": "wordmark",
            "hero_cta_label": "Order on WhatsApp",
            "hero_cta_url": "https://wa.me/15550100142",
        },
        headers=owner,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["hero_eyebrow"] == "Made to order"
    assert (body["font_preset"], body["corner_style"], body["mark_style"]) == (
        "modern",
        "pill",
        "wordmark",
    )
    assert body["hero_cta_url"] == "https://wa.me/15550100142"
    phone = client.patch(BRAND, json={"hero_cta_url": "tel:+15550100142"}, headers=owner)
    assert phone.json()["hero_cta_url"] == "tel:+15550100142"
    cleared = client.patch(
        BRAND, json={"hero_cta_label": None, "hero_cta_url": None}, headers=owner
    )
    assert (cleared.json()["hero_cta_label"], cleared.json()["hero_cta_url"]) == (None, None)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "javascript:alert(1)",
        "data:text/html,hi",
        "https://wa.me@evil.example",
        "https://user:pass@example.com",
        "https://localhost",
        "//example.com",
        "mailto:hello@example.com",
        "tel:call-me",
        "https://example.com/" + "a" * 500,
        "",
    ],
)
def test_the_second_hero_button_accepts_only_https_and_tel_links(
    client: TestClient, users, db, url: str
) -> None:
    owner = users["owner_a"].headers
    response = client.patch(BRAND, json={"hero_cta_url": url}, headers=owner)
    assert response.status_code == 422, url
    assert db.execute(
        "select hero_cta_url from public.brand_config where tenant_id = %s", (TENANT_A,)
    ).fetchone() == (None,)


@pytest.mark.parametrize(
    "bad",
    [
        {"font_preset": "comic"},
        {"corner_style": "round"},
        {"mark_style": "emoji"},
        {"hero_eyebrow": "x" * 61},
        {"collection_title": "   "},
        {"hero_cta_label": "x" * 41},
        {"font_preset": None},
    ],
)
def test_storefront_look_validation(client: TestClient, users, bad: dict) -> None:
    assert client.patch(BRAND, json=bad, headers=users["owner_a"].headers).status_code == 422


def test_storefront_settings_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    assert_access(client, users, "GET", STOREFRONT, {"owner_a": 200, "owner_b": 200})
    assert_access(
        client,
        users,
        "PATCH",
        STOREFRONT,
        {"owner_a": 200, "owner_b": 200},
        json={"contact_email": "it@example.com"},
    )


def test_storefront_settings_get_and_patch(client: TestClient, users, db) -> None:
    owner = users["owner_a"].headers
    current = client.get(STOREFRONT, headers=owner).json()
    assert current["storefront_enabled"] is True
    assert current["contact_email"] == "hello@example.com"

    updated = client.patch(
        STOREFRONT,
        json={
            "privacy_policy": "  We keep your order details only to make and ship it.  ",
            "terms": "IT terms",
            "return_policy": None,
        },
        headers=owner,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["privacy_policy"] == "We keep your order details only to make and ship it."
    assert (body["terms"], body["return_policy"]) == ("IT terms", None)
    # Tenant B is untouched.
    assert db.execute(
        "select terms from public.storefront_settings where tenant_id = %s", (TENANT_B,)
    ).fetchone() == (None,)


@pytest.mark.parametrize(
    "bad",
    [
        {"contact_email": "not an email"},
        {"terms": "x" * 20001},
        {"terms": "   "},
        {"storefront_enabled": None},
        {"tenant_id": TENANT_B},
        {},
    ],
)
def test_storefront_settings_validation(client: TestClient, users, bad: dict) -> None:
    assert client.patch(STOREFRONT, json=bad, headers=users["owner_a"].headers).status_code == 422
