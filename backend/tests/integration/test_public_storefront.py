"""Public storefront endpoints against the local stack (TRD §6.11): the tenant comes from the
Origin host, the answers carry public fields only, other tenants and unpublished products are
invisible, CORS allows exactly the storefront origins, and the per-address limit holds.

Seed: tenant A's storefront is http://localhost:5174, tenant B's http://aurora.localhost:5174.
Anything that changes a store (domains, switches, activity) uses a throwaway tenant."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import get_settings
from app.core.storefront_hosts import host_cache

from .support import TENANT_A, product_payload

pytestmark = pytest.mark.integration

ORIGIN_A = "http://localhost:5174"
ORIGIN_B = "http://aurora.localhost:5174"
SEED_PRODUCT_A = "a1000000-0000-0000-0000-000000000001"
SEED_PRODUCT_B = "b2000000-0000-0000-0000-000000000001"

STORE_KEYS = {
    "business_name", "tagline", "primary_color", "accent_color", "logo_url", "hero_eyebrow",
    "hero_title", "hero_subtitle", "collection_title", "font_preset", "corner_style",
    "mark_style", "hero_button", "currency", "contact_email", "pages", "has_made_to_order",
    "checkout_enabled", "shipping", "pickup", "payment_choices", "tax_rate_bps", "tax_shipping",
}  # fmt: skip
PRODUCT_KEYS = {
    "id", "name", "description", "metal", "price_cents", "sale_price_cents", "sale_ends_at",
    "unit_price_cents", "availability", "lead_time_note", "images", "personalization",
}  # fmt: skip
IMAGE_KEYS = {"url", "thumb_url", "width", "height", "is_primary"}


@pytest.fixture(autouse=True)
def _fresh_lookups() -> Iterator[None]:
    """Each test starts with no cached hosts and an empty request budget."""
    host_cache.clear()
    rate_limit.public_reads.reset()
    yield
    host_cache.clear()
    rate_limit.public_reads.reset()


def get(client: TestClient, path: str, origin: str | None = ORIGIN_A):
    headers = {"Origin": origin} if origin else {}
    return client.get(f"/api/v1/public{path}", headers=headers)


# ---------------------------------------------------------------------------
# Which store
# ---------------------------------------------------------------------------
def test_each_storefront_origin_gets_its_own_store(client: TestClient) -> None:
    a = get(client, "/store", ORIGIN_A)
    b = get(client, "/store", ORIGIN_B)
    assert (a.status_code, b.status_code) == (200, 200)
    assert a.json()["business_name"] == "DELEA"
    assert b.json()["business_name"] == "Aurora Gems"


@pytest.mark.parametrize(
    "origin",
    [
        None,  # no Origin at all (curl, a server)
        "https://unknown-shop.example",
        "http://localhost:5173",  # tenant A's admin domain is not a storefront
        "http://localhost:5174/evil",  # not an origin
        "null",
        "ftp://localhost:5174",
    ],
)
def test_anything_but_a_known_storefront_origin_is_store_not_found(
    client: TestClient, origin: str | None
) -> None:
    for path in ("/store", "/products", f"/products/{SEED_PRODUCT_A}", "/pages/returns"):
        response = get(client, path, origin)
        assert response.status_code == 404, (origin, path)
        assert response.json() == {"detail": "Store not found"}


def test_localhost_storefronts_are_refused_outside_development(
    client: TestClient, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "environment", "production")
    assert get(client, "/store", ORIGIN_A).status_code == 404
    assert get(client, "/store", ORIGIN_B).status_code == 404


def test_a_store_that_is_switched_off_or_inactive_disappears(
    client: TestClient, make_tenant, db
) -> None:
    tenant = make_tenant()
    origin = "http://it-shop.localhost:5174"
    db.execute(
        "insert into public.tenant_domains (domain, tenant_id, kind) "
        "values ('it-shop.localhost:5174', %s, 'storefront')",
        (tenant.id,),
    )
    # Provisioned switched off.
    assert get(client, "/store", origin).status_code == 404

    db.execute(
        "update public.storefront_settings set storefront_enabled = true where tenant_id = %s",
        (tenant.id,),
    )
    host_cache.clear()
    store = get(client, "/store", origin)
    assert store.status_code == 200
    assert store.json()["business_name"] == tenant.business_name

    db.execute("update public.tenants set is_active = false where id = %s", (tenant.id,))
    host_cache.clear()
    assert get(client, "/store", origin).status_code == 404
    db.execute("update public.tenants set is_active = true where id = %s", (tenant.id,))

    db.execute(
        "update public.storefront_settings set storefront_enabled = false where tenant_id = %s",
        (tenant.id,),
    )
    host_cache.clear()
    assert get(client, "/store", origin).status_code == 404


# ---------------------------------------------------------------------------
# Public fields only
# ---------------------------------------------------------------------------
def test_the_store_answer_has_exactly_the_public_fields(client: TestClient) -> None:
    store = get(client, "/store").json()
    assert set(store) == STORE_KEYS
    assert store["currency"] == "USD"
    assert store["checkout_enabled"] is True  # A ships, has pickup and takes bank transfer
    assert "manual_payment_instructions" not in store
    assert store["hero_eyebrow"] == "Made to order"
    assert store["hero_button"] is None  # A has no second button
    assert [p["slug"] for p in store["pages"]] == ["custom-orders", "returns"]
    text = str(store)
    for secret in ("12-3456789", "DELEA Jewelry LLC", "next_", "stripe", "000123456"):
        assert secret not in text


def test_the_second_hero_button_shows_only_with_label_and_link(client: TestClient, db) -> None:
    b = get(client, "/store", ORIGIN_B).json()
    assert b["hero_button"] == {"label": "Order on WhatsApp", "url": "https://wa.me/15550100142"}
    assert (b["font_preset"], b["corner_style"], b["mark_style"]) == ("modern", "pill", "wordmark")


def test_products_have_exactly_the_public_fields(client: TestClient) -> None:
    products = get(client, "/products").json()
    assert products, "tenant A has published products"
    for product in products:
        assert set(product) == PRODUCT_KEYS
        for image in product["images"]:
            assert set(image) == IMAGE_KEYS
    text = str(products)
    assert "DL-NK-01" not in text  # no SKU
    assert "stock_qty" not in text and "threshold" not in text


def test_the_catalog_is_published_products_in_storefront_order(
    client: TestClient, users, db
) -> None:
    owner = users["owner_a"].headers
    hidden = client.post(
        "/api/v1/products", json=product_payload(is_published=False), headers=owner
    ).json()
    shown = client.post(
        "/api/v1/products", json=product_payload(sort_order=-1, name="IT First"), headers=owner
    ).json()

    products = get(client, "/products").json()
    ids = [p["id"] for p in products]
    assert hidden["id"] not in ids
    assert ids[0] == shown["id"]  # lowest sort_order first
    assert (
        len(ids)
        == db.execute(
            "select count(*) from public.products where tenant_id = %s and is_published",
            (TENANT_A,),
        ).fetchone()[0]
    )
    assert get(client, f"/products/{hidden['id']}").status_code == 404


def test_another_tenants_product_is_not_found(client: TestClient) -> None:
    response = get(client, f"/products/{SEED_PRODUCT_B}", ORIGIN_A)
    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}
    assert get(client, f"/products/{SEED_PRODUCT_B}", ORIGIN_B).status_code == 200
    ids_b = [p["id"] for p in get(client, "/products", ORIGIN_B).json()]
    assert SEED_PRODUCT_A not in ids_b


def test_availability_price_and_made_to_order(client: TestClient, users, db) -> None:
    owner = users["owner_a"].headers
    made = client.post("/api/v1/products", json=product_payload(stock_qty=0), headers=owner).json()
    db.execute(
        "update public.products set made_to_order = true, lead_time_note = 'Ships in 2 weeks' "
        "where id = %s",
        (made["id"],),
    )
    sold_out = client.post("/api/v1/products", json=product_payload(stock_qty=0), headers=owner)
    low = client.post(
        "/api/v1/products",
        json=product_payload(stock_qty=1, sale_price_cents=9900, price_cents=12500),
        headers=owner,
    )

    def one(product_id: str) -> dict:
        response = get(client, f"/products/{product_id}")
        assert response.status_code == 200
        return response.json()

    assert one(made["id"])["availability"] == "made_to_order"
    assert one(made["id"])["lead_time_note"] == "Ships in 2 weeks"
    assert one(sold_out.json()["id"])["availability"] == "out_of_stock"
    on_sale = one(low.json()["id"])
    assert on_sale["availability"] == "low"
    assert (on_sale["price_cents"], on_sale["sale_price_cents"], on_sale["unit_price_cents"]) == (
        12500,
        9900,
        9900,
    )
    assert get(client, "/store").json()["has_made_to_order"] is True


def test_pages_with_text_only(client: TestClient) -> None:
    returns = get(client, "/pages/returns")
    assert returns.status_code == 200
    assert returns.json()["title"] == "Returns"
    assert "30 days" in returns.json()["body"]
    assert get(client, "/pages/privacy").status_code == 404  # no text yet
    assert get(client, "/pages/unknown").status_code == 422


def test_answers_are_cacheable_for_a_minute(client: TestClient) -> None:
    for path in ("/store", "/products", f"/products/{SEED_PRODUCT_A}"):
        response = get(client, path)
        assert response.headers["cache-control"] == "public, max-age=60"
        assert "origin" in response.headers["vary"].lower()


# ---------------------------------------------------------------------------
# CORS and limits
# ---------------------------------------------------------------------------
def preflight(client: TestClient, origin: str, method: str = "GET", headers: str = ""):
    request_headers = {"Origin": origin, "Access-Control-Request-Method": method}
    if headers:
        request_headers["Access-Control-Request-Headers"] = headers
    return client.options("/api/v1/public/products", headers=request_headers)


def test_a_storefront_origin_may_call_the_public_api(client: TestClient) -> None:
    for origin in (ORIGIN_A, ORIGIN_B):
        response = preflight(client, origin, "POST", "content-type")
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in response.headers
        simple = get(client, "/products", origin)
        assert simple.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    ("origin", "method", "headers"),
    [
        ("https://evil.example", "GET", ""),
        ("http://localhost:5173", "GET", ""),  # the admin origin is not a storefront
        (ORIGIN_A, "PUT", ""),
        (ORIGIN_A, "DELETE", ""),
        (ORIGIN_A, "GET", "authorization"),  # no credentials on public paths
    ],
)
def test_anything_else_is_refused_by_cors(
    client: TestClient, origin: str, method: str, headers: str
) -> None:
    response = preflight(client, origin, method, headers)
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_an_unknown_origin_gets_no_cors_header_on_a_simple_request(client: TestClient) -> None:
    response = get(client, "/products", "https://evil.example")
    assert response.status_code == 404
    assert "access-control-allow-origin" not in response.headers


def test_the_admin_origin_list_does_not_open_the_public_paths(client: TestClient) -> None:
    response = get(client, "/products", "http://localhost:5173")
    assert "access-control-allow-origin" not in response.headers


def test_too_many_requests_from_one_address(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(rate_limit.public_reads, "limit", 5)
    statuses = [get(client, "/products").status_code for _ in range(6)]
    assert statuses == [200, 200, 200, 200, 200, 429]
    refused = get(client, "/store")
    assert refused.status_code == 429
    assert int(refused.headers["retry-after"]) >= 1
    # The shop may read the 429 (to ask the shopper to wait); an unknown origin may not.
    assert refused.headers["access-control-allow-origin"] == ORIGIN_A
    stranger = get(client, "/store", "https://evil.example")
    assert stranger.status_code == 429
    assert "access-control-allow-origin" not in stranger.headers
    # Admin paths have their own rules and are not counted here.
    assert client.get("/health").status_code == 200


def test_nothing_in_the_request_but_the_origin_picks_the_shop(client: TestClient) -> None:
    """A tenant id, host or shop in the query, a Host header or a body never selects a shop."""
    for params in (
        {"shop": "aurora.localhost:5174"},
        {"tenant_id": "22222222-2222-2222-2222-222222222222"},
        {"host": "aurora.localhost:5174"},
    ):
        response = client.get("/api/v1/public/store", params=params, headers={"Origin": ORIGIN_A})
        assert response.json()["business_name"] == "DELEA", params
    hosted = client.get("/api/v1/public/store", headers={"Host": "aurora.localhost:5174"})
    assert hosted.status_code == 404
