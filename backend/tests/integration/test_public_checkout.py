"""The storefront checkout against the local stack (TRD §6.11): quotes, orders, idempotency,
bot checks, Turnstile, limits and tracking.

Every test runs on a throwaway shop (its own tenant, domain, settings and products), never on
the seed tenants A and B. The shop ships for $6 (free over $150), offers pickup, takes bank
transfer and payment on pickup, and charges 8% tax.
"""

import json
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit, turnstile
from app.core.config import get_settings
from app.core.storefront_hosts import host_cache

pytestmark = pytest.mark.integration


@dataclass
class Shop:
    tenant_id: str
    origin: str
    necklace: str  # $98, 5 in stock
    ring: str  # $145, 2 in stock, required Ring size (5-8) and optional Engraving (10)
    made: str  # $50, made to order, stock 0
    hidden: str  # unpublished
    ring_size: str  # field ids
    engraving: str


@pytest.fixture(autouse=True)
def _fresh_limits(db: psycopg.Connection) -> Iterator[None]:
    """Every test starts with empty counters: the test client is one address for all tests,
    and the checkout counts its attempts and orders in Postgres (public_rate_hits)."""
    db.execute("delete from public.public_rate_hits")
    host_cache.clear()
    for limiter in (rate_limit.public_reads, rate_limit.public_quotes, rate_limit.public_tracking):
        limiter.reset()
    yield
    host_cache.clear()
    for limiter in (rate_limit.public_reads, rate_limit.public_quotes, rate_limit.public_tracking):
        limiter.reset()


def _product(
    db: psycopg.Connection, tenant: str, name: str, price: int, stock: int, **extra
) -> str:
    columns = {"is_published": True, "made_to_order": False, "lead_time_note": None, **extra}
    return str(
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty, "
            "is_published, made_to_order, lead_time_note) "
            "values (%s, %s, %s, 'gold_14k', %s, %s, %s, %s, %s) returning id",
            (
                tenant,
                f"IT-{uuid.uuid4().hex[:10]}",
                name,
                price,
                stock,
                columns["is_published"],
                columns["made_to_order"],
                columns["lead_time_note"],
            ),
        ).fetchone()[0]
    )


def open_shop(db: psycopg.Connection, tenant_id: str, domain: str) -> None:
    db.execute(
        "insert into public.tenant_domains (domain, tenant_id, kind) values (%s, %s, 'storefront')",
        (domain, tenant_id),
    )
    db.execute(
        "update public.storefront_settings set storefront_enabled = true, ship_enabled = true, "
        "ship_flat_cents = 600, free_ship_over_cents = 15000, pickup_enabled = true, "
        "pickup_note = 'Studio, 11-6', bank_transfer_enabled = true, pay_on_pickup_enabled = true, "
        "manual_payment_instructions = 'Transfer to account 000 (test).' where tenant_id = %s",
        (tenant_id,),
    )
    db.execute(
        "update public.company_settings set default_tax_rate_bps = 800 where tenant_id = %s",
        (tenant_id,),
    )


@pytest.fixture
def shop(make_tenant, db: psycopg.Connection) -> Shop:
    tenant = make_tenant()
    domain = f"it-{uuid.uuid4().hex[:8]}.localhost:5174"
    open_shop(db, tenant.id, domain)
    ring = _product(db, tenant.id, "Halo Ring", 14500, 2)
    ring_size = str(
        db.execute(
            "insert into public.product_personalization_fields "
            "(tenant_id, product_id, position, label, kind, required, choices) "
            "values (%s, %s, 1, 'Ring size', 'choice', true, '{5,6,7,8}') returning id",
            (tenant.id, ring),
        ).fetchone()[0]
    )
    engraving = str(
        db.execute(
            "insert into public.product_personalization_fields "
            "(tenant_id, product_id, position, label, kind, required, max_length) "
            "values (%s, %s, 2, 'Engraving', 'text', false, 10) returning id",
            (tenant.id, ring),
        ).fetchone()[0]
    )
    return Shop(
        tenant_id=tenant.id,
        origin=f"http://{domain}",
        necklace=_product(db, tenant.id, "Initial Necklace", 9800, 5),
        ring=ring,
        made=_product(
            db, tenant.id, "Made Piece", 5000, 0, made_to_order=True, lead_time_note="3 weeks"
        ),
        hidden=_product(db, tenant.id, "Hidden", 1000, 5, is_published=False),
        ring_size=ring_size,
        engraving=engraving,
    )


def post(client: TestClient, shop: Shop, path: str, body: dict, origin: str | None = None):
    return client.post(
        f"/api/v1/public{path}", json=body, headers={"Origin": origin or shop.origin}
    )


def order_body(shop: Shop, **overrides) -> dict:
    body = {
        "checkout_key": str(uuid.uuid4()),
        "items": [{"product_id": shop.necklace, "qty": 1}],
        "fulfillment": "ship",
        "payment_choice": "bank_transfer",
        "expected_total_cents": 11184,  # 9800 + 600 shipping + 784 tax
        "contact": {
            "first_name": "IT-Sarah",
            "last_name": "Jenkins",
            "email": f"it-{uuid.uuid4().hex[:6]}@example.com",
            "phone": "555-0142",
        },
        "ship_to": {
            "line1": "1 Main St",
            "city": "Portland",
            "state": "me",
            "postal_code": "04101",
        },
        "note": "Gift wrap",
        "elapsed_ms": 12000,
    }
    body.update(overrides)
    return body


def count_orders(db: psycopg.Connection, shop: Shop) -> int:
    return db.execute(
        "select count(*) from public.invoices where tenant_id = %s", (shop.tenant_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Store and products
# ---------------------------------------------------------------------------
def test_the_store_lists_its_checkout_options(client: TestClient, shop: Shop) -> None:
    store = client.get("/api/v1/public/store", headers={"Origin": shop.origin}).json()
    assert store["checkout_enabled"] is True
    assert store["shipping"] == {"flat_cents": 600, "free_over_cents": 15000}
    assert store["pickup"] == {"note": "Studio, 11-6"}
    assert store["payment_choices"] == ["bank_transfer", "pay_on_pickup"]
    assert (store["tax_rate_bps"], store["tax_shipping"]) == (800, False)
    assert "Transfer to account" not in json.dumps(store)  # instructions only after an order


def test_without_a_way_to_pay_or_receive_checkout_is_off(
    client: TestClient, shop: Shop, db
) -> None:
    db.execute(
        "update public.storefront_settings set bank_transfer_enabled = false, "
        "pay_on_pickup_enabled = false where tenant_id = %s",
        (shop.tenant_id,),
    )
    assert (
        client.get("/api/v1/public/store", headers={"Origin": shop.origin}).json()[
            "checkout_enabled"
        ]
        is False
    )


def test_products_carry_their_personalization_fields(client: TestClient, shop: Shop) -> None:
    ring = client.get(
        f"/api/v1/public/products/{shop.ring}", headers={"Origin": shop.origin}
    ).json()
    assert ring["personalization"] == [
        {"id": shop.ring_size, "label": "Ring size", "kind": "choice", "required": True,
         "max_length": None, "choices": ["5", "6", "7", "8"]},
        {"id": shop.engraving, "label": "Engraving", "kind": "text", "required": False,
         "max_length": 10, "choices": None},
    ]  # fmt: skip


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("fulfillment", "qty", "shipping", "tax", "total"),
    [
        ("ship", 1, 600, 784, 11184),
        ("ship", 2, 0, 1568, 21168),  # over $150: free shipping
        ("pickup", 1, 0, 784, 10584),
        (None, 1, None, 784, 10584),  # not chosen yet
    ],
)
def test_a_quote_prices_on_the_server(
    client: TestClient, shop: Shop, fulfillment, qty, shipping, tax, total
) -> None:
    body = {"items": [{"product_id": shop.necklace, "qty": qty}], "fulfillment": fulfillment}
    quote = post(client, shop, "/checkout/quote", body).json()
    assert (quote["shipping_cents"], quote["tax_cents"], quote["total_cents"]) == (
        shipping,
        tax,
        total,
    )
    assert quote["has_problems"] is False


def test_a_quote_flags_lines_that_cant_be_ordered(client: TestClient, shop: Shop, db) -> None:
    # Three ring lines below: enough rings, so only their answers decide.
    db.execute("update public.products set stock_qty = 5 where id = %s", (shop.ring,))
    body = {
        "items": [
            {"product_id": shop.hidden, "qty": 1},
            {"product_id": shop.necklace, "qty": 6},  # 5 in stock
            {"product_id": shop.ring, "qty": 1},  # ring size missing
            {"product_id": shop.made, "qty": 9},  # made to order: fine
            {"product_id": shop.ring, "qty": 1, "personalization": {shop.ring_size: " 7 "}},
            {"product_id": shop.ring, "qty": 1, "personalization": {shop.ring_size: "9"}},
        ],
        "fulfillment": "pickup",
    }
    quote = post(client, shop, "/checkout/quote", body).json()
    problems = [line["problem"] for line in quote["lines"]]
    assert problems == ["unavailable", "unavailable", "options", None, None, "options"]
    assert quote["has_problems"] is True
    assert quote["lines"][0]["name"] is None  # nothing about an unpublished product leaks
    assert quote["lines"][3]["lead_time_note"] == "3 weeks"
    assert quote["lines"][4]["personalization"] == [{"label": "Ring size", "value": "7"}]
    text = json.dumps(quote)
    assert "stock" not in text and "Hidden" not in text


@pytest.mark.parametrize(
    "bad",
    [
        {"items": []},
        {"items": [{"product_id": "x", "qty": 1}]},
        {"items": [{"qty": 1}]},
        {"items": [{"product_id": str(uuid.uuid4()), "qty": 0}]},
        {"items": [{"product_id": str(uuid.uuid4()), "qty": 1, "unit_price_cents": 1}]},
        {"items": [{"product_id": str(uuid.uuid4()), "qty": 1}], "total_cents": 1},
        {"items": [{"product_id": str(uuid.uuid4()), "qty": 1}], "fulfillment": "drone"},
        {"items": [{"product_id": str(uuid.uuid4()), "qty": 1}], "tenant_id": str(uuid.uuid4())},
    ],
)
def test_a_quote_refuses_prices_totals_and_malformed_carts(
    client: TestClient, shop: Shop, bad: dict
) -> None:
    assert post(client, shop, "/checkout/quote", bad).status_code == 422


def test_a_quote_for_an_option_the_shop_lacks_is_refused(
    client: TestClient, shop: Shop, db
) -> None:
    db.execute(
        "update public.storefront_settings set pay_on_pickup_enabled = false, "
        "pickup_enabled = false where tenant_id = %s",
        (shop.tenant_id,),
    )
    body = {"items": [{"product_id": shop.necklace, "qty": 1}], "fulfillment": "pickup"}
    response = post(client, shop, "/checkout/quote", body)
    assert response.status_code == 422
    assert response.json()["detail"] == "This shop doesn't offer that delivery option."


def test_quotes_are_limited_per_address(client: TestClient, shop: Shop, monkeypatch) -> None:
    monkeypatch.setattr(rate_limit.public_quotes, "limit", 3)
    body = {"items": [{"product_id": shop.necklace, "qty": 1}]}
    codes = [post(client, shop, "/checkout/quote", body).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------
def test_an_order_is_placed_unpaid_with_the_servers_amounts(
    client: TestClient, shop: Shop, db
) -> None:
    body = order_body(shop)
    response = post(client, shop, "/orders", body)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["order_ref"].startswith("W-")
    assert order["status"] == "awaiting_payment"
    amounts = ("subtotal_cents", "shipping_cents", "tax_cents", "total_cents")
    assert [order[k] for k in amounts] == [9800, 600, 784, 11184]
    assert order["payment_instructions"] == "Transfer to account 000 (test)."
    assert (order["first_name"], order["ship_city"], order["ship_state"]) == (
        "IT-Sarah",
        "Portland",
        "ME",
    )
    assert "Main St" not in json.dumps(order)  # the street is never in the shopper's view
    assert response.headers["cache-control"] == "no-store"

    row = db.execute(
        "select i.order_status::text, i.source, i.total_cents, c.ship_line1, c.email, "
        "cu.customer_type::text from public.invoices i "
        "join public.order_contacts c on c.invoice_id = i.id "
        "join public.customers cu on cu.id = i.customer_id where i.public_token = %s",
        (order["public_token"],),
    ).fetchone()
    assert row == ("quote", "storefront", 11184, "1 Main St", body["contact"]["email"], "retail")
    assert db.execute(
        "select stock_qty from public.products where id = %s", (shop.necklace,)
    ).fetchone() == (5,)


def test_personalization_answers_are_stored_on_the_line(client: TestClient, shop: Shop, db) -> None:
    body = order_body(
        shop,
        items=[
            {
                "product_id": shop.ring,
                "qty": 1,
                "personalization": {shop.ring_size: "7", shop.engraving: "K&J"},
            }
        ],
        fulfillment="pickup",
        payment_choice="pay_on_pickup",
        ship_to=None,
        expected_total_cents=14500 + 1160,
    )
    response = post(client, shop, "/orders", body)
    assert response.status_code == 201, response.text
    assert response.json()["lines"][0]["personalization"] == [
        {"label": "Ring size", "value": "7"},
        {"label": "Engraving", "value": "K&J"},
    ]
    assert response.json()["pickup_note"] == "Studio, 11-6"
    # Editing the field later never changes the order: the label was copied.
    db.execute(
        "update public.product_personalization_fields set label = 'Size' where id = %s",
        (shop.ring_size,),
    )
    tracked = client.get(
        f"/api/v1/public/orders/{response.json()['public_token']}", headers={"Origin": shop.origin}
    )
    assert tracked.json()["lines"][0]["personalization"][0]["label"] == "Ring size"


def test_the_same_checkout_twice_is_one_order(client: TestClient, shop: Shop, db) -> None:
    body = order_body(shop)
    first = post(client, shop, "/orders", body)
    again = post(
        client, shop, "/orders", {**body, "turnstile_token": "a-new-token", "elapsed_ms": 20000}
    )
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["public_token"] == first.json()["public_token"]
    changed = post(client, shop, "/orders", {**body, "note": "Different"})
    assert changed.status_code == 409
    assert count_orders(db, shop) == 1


def test_a_double_click_at_the_same_moment_is_one_order(client: TestClient, shop: Shop, db) -> None:
    body = order_body(shop)
    results: list[int] = []

    def send() -> None:
        results.append(post(client, shop, "/orders", body).status_code)

    threads = [threading.Thread(target=send) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 200, 200, 201]
    assert count_orders(db, shop) == 1


def test_a_changed_price_is_never_charged_silently(client: TestClient, shop: Shop, db) -> None:
    response = post(client, shop, "/orders", order_body(shop, expected_total_cents=10000))
    assert response.status_code == 409
    assert response.json()["reason"] == "price_changed"
    assert response.json()["quote"]["total_cents"] == 11184
    assert count_orders(db, shop) == 0


def test_unavailable_items_are_refused_with_the_lines_to_fix(
    client: TestClient, shop: Shop, db
) -> None:
    body = order_body(shop, items=[{"product_id": shop.necklace, "qty": 6}])
    response = post(client, shop, "/orders", body)
    assert response.status_code == 409
    assert response.json()["reason"] == "unavailable"
    assert response.json()["quote"]["lines"][0]["problem"] == "unavailable"
    # No stock numbers in the answer, unlike the admin's message from the database.
    assert response.json()["detail"] == "Some items are no longer available or need your choices."
    assert count_orders(db, shop) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"total_cents": 1},
        {"unit_price_cents": 1},
        {"tenant_id": str(uuid.uuid4())},
        {"order_status": "paid"},
        {"fulfillment": "ship", "ship_to": None},
        {"fulfillment": "pickup", "payment_choice": "bank_transfer"},  # pickup with an address
        {"fulfillment": "ship", "payment_choice": "pay_on_pickup"},
        {"payment_choice": "card"},
        {"contact": {"first_name": "", "last_name": "X", "email": "a@example.com"}},
        {"contact": {"first_name": "A", "last_name": "X", "email": "not-an-email"}},
        {"ship_to": {"line1": "1 Main St", "city": "Toronto", "state": "ON", "postal_code": "M5V"}},
        {
            "items": [
                {
                    "product_id": str(uuid.uuid4()),
                    "qty": 1,
                    "personalization": {str(uuid.uuid4()): "x" * 201},
                }
            ]
        },
        {"elapsed_ms": 500},  # filled faster than a person can
        {"website": "http://spam.example"},  # the hidden field
    ],
)
def test_bad_or_suspicious_orders_are_refused(
    client: TestClient, shop: Shop, db, change: dict
) -> None:
    response = post(client, shop, "/orders", order_body(shop, **change))
    assert response.status_code == 422, (change, response.text)
    assert count_orders(db, shop) == 0


def test_a_wholesale_email_still_buys_at_retail(client: TestClient, shop: Shop, db) -> None:
    db.execute(
        "insert into public.customers (tenant_id, customer_type, first_name, company_name, email, "
        "wholesale_discount_bps, tax_exempt) values (%s, 'wholesale', 'IT-Buyer', 'Big Shop', "
        "'it-wholesale@example.com', 2500, true)",
        (shop.tenant_id,),
    )
    body = order_body(
        shop, contact={"first_name": "IT-W", "last_name": "B", "email": "IT-Wholesale@example.com"}
    )
    order = post(client, shop, "/orders", body).json()
    assert (order["tax_cents"], order["total_cents"]) == (784, 11184)


def test_orders_per_email_are_limited(client: TestClient, shop: Shop) -> None:
    email = f"it-{uuid.uuid4().hex[:6]}@example.com"
    contact = {"first_name": "IT-A", "last_name": "B", "email": email}
    codes = [
        post(client, shop, "/orders", order_body(shop, contact=contact)).status_code
        for _ in range(4)
    ]
    assert codes == [201, 201, 201, 429]


def test_order_attempts_per_address_are_limited(client: TestClient, shop: Shop) -> None:
    codes = [
        post(client, shop, "/orders", order_body(shop, expected_total_cents=1)).status_code
        for _ in range(21)
    ]
    assert codes[:20] == [409] * 20  # wrong total each time, but each attempt counts
    assert codes[20] == 429


def test_a_closed_shop_takes_no_orders(client: TestClient, shop: Shop, db) -> None:
    db.execute(
        "update public.storefront_settings set storefront_enabled = false where tenant_id = %s",
        (shop.tenant_id,),
    )
    host_cache.clear()
    assert post(client, shop, "/orders", order_body(shop)).status_code == 404
    assert count_orders(db, shop) == 0


def test_another_shops_origin_never_places_an_order_here(
    client: TestClient, shop: Shop, db
) -> None:
    # The products belong to the throwaway shop; tenant A's storefront can't order them.
    response = post(client, shop, "/orders", order_body(shop), origin="http://localhost:5174")
    assert response.status_code == 409
    assert response.json()["reason"] == "unavailable"
    assert count_orders(db, shop) == 0


# ---------------------------------------------------------------------------
# Turnstile
# ---------------------------------------------------------------------------
def _cloudflare(monkeypatch, answer) -> list[dict]:
    """Answer the siteverify call with `answer` (a dict, or an exception to raise)."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(httpx.QueryParams(request.content.decode())))
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(200, json=answer)

    monkeypatch.setattr(turnstile, "transport", httpx.MockTransport(handler))
    monkeypatch.setattr(get_settings(), "turnstile_secret_key", "it-test-secret")
    return seen


def test_a_passing_token_places_the_order(client: TestClient, shop: Shop, monkeypatch) -> None:
    seen = _cloudflare(monkeypatch, {"success": True, "action": "checkout", "hostname": "x"})
    response = post(client, shop, "/orders", order_body(shop, turnstile_token="tok-1"))
    assert response.status_code == 201
    assert seen[0]["response"] == "tok-1" and seen[0]["secret"] == "it-test-secret"


@pytest.mark.parametrize(
    "answer",
    [
        {"success": False, "error-codes": ["invalid-input-response"]},
        {"success": True, "action": "login", "hostname": "x"},
    ],
)
def test_a_failing_token_is_refused(
    client: TestClient, shop: Shop, db, monkeypatch, answer
) -> None:
    _cloudflare(monkeypatch, answer)
    response = post(client, shop, "/orders", order_body(shop, turnstile_token="tok"))
    assert response.status_code == 422
    assert count_orders(db, shop) == 0


def test_a_missing_token_is_refused_when_a_key_is_set(
    client: TestClient, shop: Shop, db, monkeypatch
) -> None:
    _cloudflare(monkeypatch, {"success": True, "action": "checkout"})
    assert post(client, shop, "/orders", order_body(shop)).status_code == 422
    assert count_orders(db, shop) == 0


def test_cloudflare_unreachable_refuses_the_order(
    client: TestClient, shop: Shop, db, monkeypatch
) -> None:
    _cloudflare(monkeypatch, httpx.ConnectError("down"))
    response = post(client, shop, "/orders", order_body(shop, turnstile_token="tok"))
    assert response.status_code == 503
    assert count_orders(db, shop) == 0


@pytest.fixture
def hosted_shop(shop: Shop, db) -> Shop:
    """The same shop on an https:// domain, so it can be reached in a non-local environment."""
    domain = f"it-{uuid.uuid4().hex[:8]}.example.test"
    db.execute(
        "insert into public.tenant_domains (domain, tenant_id, kind) values (%s, %s, 'storefront')",
        (domain, shop.tenant_id),
    )
    shop.origin = f"https://{domain}"
    return shop


def test_production_without_a_key_has_no_checkout(
    client: TestClient, hosted_shop: Shop, db, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(get_settings(), "turnstile_secret_key", "")
    store = client.get("/api/v1/public/store", headers={"Origin": hosted_shop.origin}).json()
    assert store["checkout_enabled"] is False
    response = post(client, hosted_shop, "/orders", order_body(hosted_shop))
    assert response.status_code == 503
    assert count_orders(db, hosted_shop) == 0


def test_production_checks_the_tokens_hostname(
    client: TestClient, hosted_shop: Shop, db, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "environment", "production")
    host = hosted_shop.origin.removeprefix("https://")
    _cloudflare(
        monkeypatch, {"success": True, "action": "checkout", "hostname": "other-site.example"}
    )
    assert (
        post(
            client, hosted_shop, "/orders", order_body(hosted_shop, turnstile_token="t")
        ).status_code
        == 422
    )
    _cloudflare(monkeypatch, {"success": True, "action": "checkout", "hostname": host})
    assert (
        post(
            client, hosted_shop, "/orders", order_body(hosted_shop, turnstile_token="t")
        ).status_code
        == 201
    )


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------
def test_tracking_shows_one_order_in_its_own_shop(client: TestClient, shop: Shop, db) -> None:
    token = post(client, shop, "/orders", order_body(shop)).json()["public_token"]
    url = f"/api/v1/public/orders/{token}"
    tracked = client.get(url, headers={"Origin": shop.origin})
    assert tracked.status_code == 200
    assert tracked.headers["referrer-policy"] == "no-referrer"
    assert tracked.headers["cache-control"] == "no-store"
    assert tracked.json()["status"] == "awaiting_payment"

    assert client.get(url, headers={"Origin": "http://localhost:5174"}).status_code == 404
    assert (
        client.get(
            f"/api/v1/public/orders/{uuid.uuid4()}", headers={"Origin": shop.origin}
        ).status_code
        == 404
    )
    assert client.get(url).status_code == 404  # no Origin

    db.execute(
        "update public.invoices set order_status = 'paid', payment_method = 'bank_transfer' "
        "where public_token = %s",
        (token,),
    )
    paid = client.get(url, headers={"Origin": shop.origin}).json()
    assert (paid["status"], paid["payment_instructions"]) == ("paid", None)
    db.execute("update public.invoices set refunded_at = now() where public_token = %s", (token,))
    assert client.get(url, headers={"Origin": shop.origin}).json()["status"] == "refunded"


def test_tracking_is_limited_per_address(client: TestClient, shop: Shop, monkeypatch) -> None:
    monkeypatch.setattr(rate_limit.public_tracking, "limit", 2)
    url = f"/api/v1/public/orders/{uuid.uuid4()}"
    codes = [client.get(url, headers={"Origin": shop.origin}).status_code for _ in range(3)]
    assert codes == [404, 404, 429]


def test_the_storefront_may_post_to_the_checkout(client: TestClient, shop: Shop) -> None:
    response = client.options(
        "/api/v1/public/orders",
        headers={
            "Origin": shop.origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == shop.origin
