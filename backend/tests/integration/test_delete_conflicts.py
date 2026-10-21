"""Deletes that would silently damage history are refused with a clear 409; deletes with no
such reference still work. Seed: the necklace (a1…01) is on Sarah's paid invoice; Sarah
(a5…01) has an invoice; 925 Silver blanks (a3…01) are used by the Engagement Ring."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TestUser

pytestmark = pytest.mark.integration

SEED_NECKLACE = "a1000000-0000-0000-0000-000000000001"
SEED_SARAH = "a5000000-0000-0000-0000-000000000001"
SEED_SILVER_BLANKS = "a3000000-0000-0000-0000-000000000001"


def it(prefix: str) -> str:
    return f"IT-{prefix}-{uuid.uuid4().hex[:6]}"


def exists(db: psycopg.Connection, table: str, row_id: str) -> bool:
    return db.execute(
        f"select exists (select 1 from public.{table} where id = %s)", (row_id,)
    ).fetchone()[0]


def new_product(db: psycopg.Connection) -> str:
    return str(
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents) "
            "values (%s, %s, 'IT product', 'other', 100) returning id",
            (TENANT_A, it("SKU")),
        ).fetchone()[0]
    )


def test_product_on_a_paid_invoice_line_is_409(client: TestClient, users, db) -> None:
    response = client.delete(f"/api/v1/products/{SEED_NECKLACE}", headers=users["owner_a"].headers)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This product appears on 1 invoice line(s). "
        "Unpublish it instead (set is_published to false)."
    }
    assert exists(db, "products", SEED_NECKLACE)


def test_product_on_a_quote_line_is_409(client: TestClient, users: dict[str, TestUser], db) -> None:
    product_id = new_product(db)
    invoice_id = db.execute(
        "insert into public.invoices (tenant_id, customer_name) values (%s, %s) returning id",
        (TENANT_A, it("quote")),
    ).fetchone()[0]
    db.execute(
        "insert into public.invoice_line_items (invoice_id, tenant_id, product_id, description, "
        "qty, unit_price_cents, line_total_cents) values (%s, %s, %s, 'IT', 2, 100, 200)",
        (invoice_id, TENANT_A, product_id),
    )
    response = client.delete(f"/api/v1/products/{product_id}", headers=users["owner_a"].headers)
    assert response.status_code == 409
    assert "appears on 1 invoice line(s)" in response.json()["detail"]
    # The quote's line still points at the product (it will decrement stock when paid).
    assert (
        db.execute(
            "select product_id::text from public.invoice_line_items where invoice_id = %s",
            (invoice_id,),
        ).fetchone()[0]
        == product_id
    )


def test_unsold_product_can_be_deleted(client: TestClient, users, db) -> None:
    product_id = new_product(db)
    response = client.delete(f"/api/v1/products/{product_id}", headers=users["owner_a"].headers)
    assert response.status_code == 204
    assert not exists(db, "products", product_id)


def test_customer_with_invoices_is_409(client: TestClient, users, db) -> None:
    response = client.delete(f"/api/v1/customers/{SEED_SARAH}", headers=users["owner_a"].headers)
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This customer has 1 invoice(s); deleting would detach them from their history."
    }
    assert exists(db, "customers", SEED_SARAH)


def test_customer_without_invoices_can_be_deleted(client: TestClient, users, db) -> None:
    customer_id = str(
        db.execute(
            "insert into public.customers (tenant_id, first_name) values (%s, %s) returning id",
            (TENANT_A, it("cust")),
        ).fetchone()[0]
    )
    response = client.delete(f"/api/v1/customers/{customer_id}", headers=users["owner_a"].headers)
    assert response.status_code == 204
    assert not exists(db, "customers", customer_id)


def test_material_used_by_products_is_409(client: TestClient, users, db) -> None:
    response = client.delete(
        f"/api/v1/materials/{SEED_SILVER_BLANKS}", headers=users["owner_a"].headers
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": "This material is used by 1 product(s); remove it from those products first."
    }
    assert exists(db, "materials", SEED_SILVER_BLANKS)


def test_material_only_on_the_reorder_list_can_be_deleted(client: TestClient, users, db) -> None:
    material_id = str(
        db.execute(
            "insert into public.materials (tenant_id, name, unit, unit_cost_cents) "
            "values (%s, %s, 'pcs', 1) returning id",
            (TENANT_A, it("mat")),
        ).fetchone()[0]
    )
    db.execute(
        "insert into public.reorder_list (tenant_id, material_id) values (%s, %s)",
        (TENANT_A, material_id),
    )
    response = client.delete(f"/api/v1/materials/{material_id}", headers=users["owner_a"].headers)
    assert response.status_code == 204
    assert not exists(db, "materials", material_id)


def test_missing_rows_are_404_before_any_check(client: TestClient, users) -> None:
    owner = users["owner_a"].headers
    missing = "00000000-0000-0000-0000-000000000000"
    for kind in ("products", "customers", "materials"):
        assert client.delete(f"/api/v1/{kind}/{missing}", headers=owner).status_code == 404
