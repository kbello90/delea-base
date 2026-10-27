"""Website orders in the admin (Slice A2): what owner, staff and CPA see, the awaiting-payment
count and filter, the locked lines, confirming payment, and the CSV's shipping column.

Runs on a throwaway shop with an owner, staff and CPA; orders are placed through the public
checkout, as a shopper would."""

import csv
import io
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.storefront_hosts import host_cache

from .support import ThrowawayTenant
from .test_public_checkout import open_shop

pytestmark = pytest.mark.integration

INVOICES = "/api/v1/invoices"


@pytest.fixture(autouse=True)
def _fresh_limits(db: psycopg.Connection) -> Iterator[None]:
    db.execute("delete from public.public_rate_hits")
    host_cache.clear()
    rate_limit.public_reads.reset()
    yield
    host_cache.clear()


@pytest.fixture
def shop(make_tenant, db: psycopg.Connection) -> dict:
    tenant: ThrowawayTenant = make_tenant(roles=("owner", "staff", "cpa"))
    domain = f"it-{uuid.uuid4().hex[:8]}.localhost:5174"
    open_shop(db, tenant.id, domain)
    ring = db.execute(
        "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty, "
        "is_published) values (%s, %s, 'Halo Ring', 'gold_14k', 14500, 3, true) returning id",
        (tenant.id, f"IT-{uuid.uuid4().hex[:10]}"),
    ).fetchone()[0]
    size = db.execute(
        "insert into public.product_personalization_fields "
        "(tenant_id, product_id, position, label, kind, required, choices) "
        "values (%s, %s, 1, 'Ring size', 'choice', true, '{6,7}') returning id",
        (tenant.id, ring),
    ).fetchone()[0]
    return {"tenant": tenant, "origin": f"http://{domain}", "ring": str(ring), "size": str(size)}


def place(client: TestClient, shop: dict, **overrides) -> dict:
    body = {
        "checkout_key": str(uuid.uuid4()),
        "items": [{"product_id": shop["ring"], "qty": 1, "personalization": {shop["size"]: "7"}}],
        "fulfillment": "ship",
        "payment_choice": "bank_transfer",
        "expected_total_cents": 14500 + 600 + 1160,
        "contact": {
            "first_name": "IT-Priya",
            "last_name": "Nair",
            "email": f"it-{uuid.uuid4().hex[:6]}@example.com",
        },
        "ship_to": {"line1": "9 Elm St", "city": "Austin", "state": "TX", "postal_code": "78701"},
        "elapsed_ms": 9000,
        **overrides,
    }
    response = client.post("/api/v1/public/orders", json=body, headers={"Origin": shop["origin"]})
    assert response.status_code == 201, response.text
    return response.json()


def _invoice_id(db, token: str) -> str:
    return str(
        db.execute("select id from public.invoices where public_token = %s", (token,)).fetchone()[0]
    )


def test_owner_and_staff_see_the_shopper_the_cpa_does_not(client: TestClient, shop, db) -> None:
    order = place(client, shop)
    invoice_id = _invoice_id(db, order["public_token"])
    tenant: ThrowawayTenant = shop["tenant"]
    for role in ("owner", "staff"):
        invoice = client.get(f"{INVOICES}/{invoice_id}", headers=tenant.headers(role)).json()
        assert invoice["source"] == "storefront"
        assert invoice["order_ref"] == order["order_ref"]
        assert (invoice["shipping_cents"], invoice["fulfillment"], invoice["payment_choice"]) == (
            600,
            "ship",
            "bank_transfer",
        )
        assert invoice["contact"]["ship_line1"] == "9 Elm St"
        assert invoice["items"][0]["personalization"] == [{"label": "Ring size", "value": "7"}]
    cpa = client.get(f"{INVOICES}/{invoice_id}", headers=tenant.headers("cpa")).json()
    assert (cpa["items"], cpa["contact"]) == (None, None)
    assert cpa["total_cents"] == 14500 + 600 + 1160


def test_the_awaiting_payment_count_and_filter(client: TestClient, shop, db) -> None:
    tenant: ThrowawayTenant = shop["tenant"]
    owner = tenant.headers("owner")
    assert client.get(f"{INVOICES}/counts", headers=owner).json() == {"awaiting_payment_web": 0}

    waiting = place(client, shop)
    cancelled = place(client, shop)
    paid = place(client, shop)
    client.post(f"{INVOICES}/{_invoice_id(db, cancelled['public_token'])}/cancel", headers=owner)
    client.post(
        f"{INVOICES}/{_invoice_id(db, paid['public_token'])}/status",
        json={"status": "paid", "payment_method": "bank_transfer"},
        headers=owner,
    )
    # An admin quote is never counted.
    db.execute(
        "insert into public.invoices (tenant_id, customer_name) values (%s, 'IT-Admin quote')",
        (tenant.id,),
    )

    for role in ("owner", "staff"):
        counts = client.get(f"{INVOICES}/counts", headers=tenant.headers(role))
        assert counts.json() == {"awaiting_payment_web": 1}
    assert client.get(f"{INVOICES}/counts", headers=tenant.headers("cpa")).status_code == 403

    listed = client.get(INVOICES, params={"awaiting_payment": "true"}, headers=owner).json()
    assert [i["order_ref"] for i in listed] == [waiting["order_ref"]]
    web = client.get(INVOICES, params={"source": "storefront"}, headers=owner).json()
    assert len(web) == 3
    admin = client.get(INVOICES, params={"source": "admin"}, headers=owner).json()
    assert [i["customer_name"] for i in admin] == ["IT-Admin quote"]
    found = client.get(INVOICES, params={"q": waiting["order_ref"]}, headers=owner).json()
    assert [i["order_ref"] for i in found] == [waiting["order_ref"]]
    assert client.get(INVOICES, params={"source": "pos"}, headers=owner).status_code == 422


def test_an_order_holding_a_deposit_is_not_awaiting_payment(client: TestClient, shop, db) -> None:
    """The badge and the Awaiting payment filter count orders with nothing paid. A deposit
    order shows its own tag and balance instead (B1 follow-up)."""
    tenant: ThrowawayTenant = shop["tenant"]
    owner = tenant.headers("owner")
    waiting = place(client, shop)
    deposit = place(client, shop)
    recorded = client.post(
        f"{INVOICES}/{_invoice_id(db, deposit['public_token'])}/payments",
        json={"amount_cents": 1000, "method": "bank_transfer"},
        headers=owner,
    )
    assert recorded.status_code == 201, recorded.text
    assert client.get(f"{INVOICES}/counts", headers=owner).json() == {"awaiting_payment_web": 1}
    listed = client.get(INVOICES, params={"awaiting_payment": "true"}, headers=owner).json()
    assert [i["order_ref"] for i in listed] == [waiting["order_ref"]]


def test_another_tenant_never_counts_or_sees_these_orders(client: TestClient, shop, users) -> None:
    place(client, shop)
    owner_a = users["owner_a"].headers
    before = client.get(f"{INVOICES}/counts", headers=owner_a).json()
    assert before == {"awaiting_payment_web": 0}  # tenant A has no website orders in the seed
    assert client.get(INVOICES, params={"source": "storefront"}, headers=owner_a).json() == []


def test_website_order_lines_are_locked(client: TestClient, shop, db) -> None:
    order = place(client, shop)
    invoice_id = _invoice_id(db, order["public_token"])
    owner = shop["tenant"].headers("owner")
    items = {"items": [{"product_id": shop["ring"], "qty": 2}]}
    for path in (f"{INVOICES}/{invoice_id}/items", f"{INVOICES}/{invoice_id}/items/preview"):
        method = client.put if path.endswith("items") else client.post
        response = method(path, json=items, headers=owner)
        assert response.status_code == 409, path
        assert response.json()["detail"].startswith("Website orders can't be edited")
    assert client.get(f"{INVOICES}/{invoice_id}", headers=owner).json()["total_cents"] == 16260


def test_confirming_payment_makes_it_an_invoice_and_takes_stock(
    client: TestClient, shop, db
) -> None:
    order = place(client, shop)
    invoice_id = _invoice_id(db, order["public_token"])
    staff = shop["tenant"].headers("staff")
    confirmed = client.post(
        f"{INVOICES}/{invoice_id}/status",
        json={"status": "paid", "payment_method": "bank_transfer"},
        headers=staff,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["invoice_number"].startswith("INV-")
    assert db.execute(
        "select stock_qty from public.products where id = %s", (shop["ring"],)
    ).fetchone() == (2,)
    tracked = client.get(
        f"/api/v1/public/orders/{order['public_token']}", headers={"Origin": shop["origin"]}
    ).json()
    assert (tracked["status"], tracked["payment_instructions"]) == ("paid", None)


def test_the_csv_has_a_shipping_column_last(client: TestClient, shop) -> None:
    place(client, shop)
    response = client.get(f"{INVOICES}/export.csv", headers=shop["tenant"].headers("owner"))
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [(r["subtotal"], r["tax"], r["shipping"], r["total"], r["source"]) for r in rows] == [
        ("145.00", "11.60", "6.00", "162.60", "storefront")
    ]
    # Last, so every earlier column keeps the position it had before Slice A2.
    header = response.text.splitlines()[0].split(",")
    assert header[-1] == "shipping"
    assert header[:-1] == [
        "quote_number", "invoice_number", "order_status", "customer_name", "created_at",
        "paid_at", "payment_method", "due_date", "currency", "subtotal", "tax_rate_bps", "tax",
        "total", "refunded_at", "cancelled_at", "source",
    ]  # fmt: skip
