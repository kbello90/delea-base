"""Invoice API with real tokens and both tenants: role matrix, server-computed money,
quote-only edits, status moves with stock, pay_now atomicity, refunds, CSV.

Every product and customer is created per test with the IT- prefix; their invoices (and
walk-in invoices) are removed afterwards and the document counters restored (conftest)."""

import csv
import io
import uuid
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

INVOICES = "/api/v1/invoices"
SEED_B_INVOICE = "b8000000-0000-0000-0000-000000000001"
SEED_B_PRODUCT = "b2000000-0000-0000-0000-000000000001"
SEED_B_CUSTOMER = "b6000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
def _remove_walk_in_invoices(db: psycopg.Connection) -> Iterator[None]:
    yield
    db.execute("delete from public.invoices where customer_name = 'Walk-in customer'")


def tag() -> str:
    return uuid.uuid4().hex[:6].upper()


def product(db, price=10000, sale=None, ends=None, stock=100, tenant=TENANT_A) -> str:
    return str(
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents, "
            "sale_price_cents, sale_ends_at, stock_qty) "
            "values (%s, %s, %s, 'other', %s, %s, %s, %s) returning id",
            (tenant, f"IT-{tag()}", f"IT ring {tag()}", price, sale, ends, stock),
        ).fetchone()[0]
    )


def customer(db, wholesale=False, bps=0, exempt=False, terms="due_on_receipt") -> str:
    if wholesale:
        row = db.execute(
            "insert into public.customers (tenant_id, customer_type, first_name, company_name, "
            "tax_exempt, resale_certificate_number, wholesale_discount_bps, payment_terms) "
            "values (%s, 'wholesale', %s, %s, %s, %s, %s, %s) returning id",
            (
                TENANT_A,
                f"IT-buyer-{tag()}",
                f"IT-Co-{tag()}",
                exempt,
                "RC-1" if exempt else None,
                bps,
                terms,
            ),
        ).fetchone()
    else:
        row = db.execute(
            "insert into public.customers (tenant_id, first_name, last_name) "
            "values (%s, %s, 'Tester') returning id",
            (TENANT_A, f"IT-{tag()}"),
        ).fetchone()
    return str(row[0])


def stock(db, product_id: str) -> int:
    return db.execute(
        "select stock_qty from public.products where id = %s", (product_id,)
    ).fetchone()[0]


def counters(db, tenant=TENANT_A) -> tuple[int, int]:
    return db.execute(
        "select next_quote_number, next_invoice_number from public.company_settings "
        "where tenant_id = %s",
        (tenant,),
    ).fetchone()


def create(client, user: TestUser, items, status_code=201, **extra) -> dict:
    response = client.post(INVOICES, json={"items": items, **extra}, headers=user.headers)
    assert response.status_code == status_code, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Role matrix (every endpoint x owner, staff, cpa, no membership, tenant B)
# ---------------------------------------------------------------------------
def test_role_matrix(client: TestClient, users: dict[str, TestUser], db) -> None:
    owner = users["owner_a"]
    p = product(db)
    body = {"items": [{"product_id": p, "qty": 1}], "customer_id": customer(db)}
    quote = create(client, owner, body["items"], customer_id=body["customer_id"])
    url = f"{INVOICES}/{quote['id']}"

    read = {"owner_a": 200, "staff_a": 200, "cpa_a": 200}
    assert_access(client, users, "GET", INVOICES, {**read, "owner_b": 200})
    assert_access(client, users, "GET", f"{INVOICES}/export.csv", {**read, "owner_b": 200})
    assert_access(client, users, "GET", url, {**read, "owner_b": 404})
    # Tenant B can't use A's products or customer: 404, nothing created in B.
    assert_access(
        client, users, "POST", INVOICES, {"owner_a": 201, "staff_a": 201, "owner_b": 404}, json=body
    )
    assert_access(
        client,
        users,
        "PUT",
        f"{url}/items",
        {"owner_a": 200, "staff_a": 200, "owner_b": 404},
        json={"items": [{"product_id": p, "qty": 2}]},
    )
    assert_access(
        client,
        users,
        "POST",
        f"{url}/status",
        {"owner_a": 200, "staff_a": 200, "owner_b": 404},
        json={"status": "quote"},
    )

    paid = create(client, owner, body["items"], pay_now=True)
    assert_access(
        client, users, "POST", f"{INVOICES}/{paid['id']}/refund", {"owner_a": 200, "owner_b": 404}
    )
    other = create(client, owner, body["items"])
    assert_access(
        client, users, "POST", f"{INVOICES}/{other['id']}/cancel", {"owner_a": 200, "owner_b": 404}
    )
    doomed = create(client, owner, body["items"])
    assert_access(
        client, users, "DELETE", f"{INVOICES}/{doomed['id']}", {"owner_a": 204, "owner_b": 404}
    )


def test_cpa_reads_without_line_items_and_cannot_write(client, users, db) -> None:
    quote = create(client, users["owner_a"], [{"product_id": product(db), "qty": 1}])
    cpa = users["cpa_a"]
    one = client.get(f"{INVOICES}/{quote['id']}", headers=cpa.headers).json()
    assert one["items"] is None and one["total_cents"] == quote["total_cents"]
    owner_view = client.get(f"{INVOICES}/{quote['id']}", headers=users["owner_a"].headers).json()
    assert len(owner_view["items"]) == 1
    url = f"{INVOICES}/{quote['id']}"
    for method, path, body in [
        ("POST", INVOICES, {"items": [{"product_id": product(db), "qty": 1}]}),
        ("PUT", f"{url}/items", {"items": [{"product_id": product(db), "qty": 1}]}),
        ("POST", f"{url}/status", {"status": "paid"}),
        ("POST", f"{url}/refund", None),
        ("POST", f"{url}/cancel", None),
        ("DELETE", url, None),
    ]:
        assert client.request(method, path, json=body, headers=cpa.headers).status_code == 403
    assert (
        db.execute(
            "select order_status::text from public.invoices where id = %s", (quote["id"],)
        ).fetchone()[0]
        == "quote"
    )


def test_cross_tenant_references_are_404(client, users, db) -> None:
    owner_a, owner_b = users["owner_a"], users["owner_b"]
    assert client.get(f"{INVOICES}/{SEED_B_INVOICE}", headers=owner_a.headers).status_code == 404
    before = counters(db)
    response = client.post(
        INVOICES,
        json={"items": [{"product_id": SEED_B_PRODUCT, "qty": 1}]},
        headers=owner_a.headers,
    )
    assert response.status_code == 404 and "not found" in response.json()["detail"]
    response = client.post(
        INVOICES,
        json={"customer_id": SEED_B_CUSTOMER, "items": [{"product_id": product(db), "qty": 1}]},
        headers=owner_a.headers,
    )
    assert response.status_code == 404
    assert counters(db) == before  # nothing created, no number used
    listed = client.get(INVOICES, params={"limit": 100}, headers=owner_b.headers).json()
    assert {i["tenant_id"] for i in listed} == {TENANT_B}


# ---------------------------------------------------------------------------
# The client can't set money, status or numbers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "forged",
    [
        {"subtotal_cents": 1},
        {"tax_cents": 0},
        {"total_cents": 1},
        {"order_status": "paid"},
        {"quote_number": "QTE-X"},
        {"invoice_number": "INV-X"},
        {"tenant_id": TENANT_B},
        {"paid_at": "2026-10-20T00:00:00Z"},
    ],
)
def test_forged_fields_are_422_and_create_nothing(client, users, db, forged: dict) -> None:
    cust = customer(db)
    response = client.post(
        INVOICES,
        json={"customer_id": cust, "items": [{"product_id": product(db), "qty": 1}], **forged},
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 422
    assert (
        db.execute(
            "select count(*) from public.invoices where customer_id = %s", (cust,)
        ).fetchone()[0]
        == 0
    )


def test_forged_line_price_is_422(client, users, db) -> None:
    item = {"product_id": product(db), "qty": 1, "unit_price_cents": 1}
    response = client.post(INVOICES, json={"items": [item]}, headers=users["owner_a"].headers)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Pricing (exact cents)
# ---------------------------------------------------------------------------
def test_retail_quote_uses_active_sale_and_tax_once(client, users, db) -> None:
    on_sale = product(db, price=10000, sale=9000)  # open-ended sale
    expired = product(db, price=5000, sale=1000, ends="2020-01-01T00:00:00Z")
    quote = create(
        client,
        users["staff_a"],
        [{"product_id": on_sale, "qty": 2}, {"product_id": expired, "qty": 1}],
        customer_id=customer(db),
    )
    units = sorted(i["unit_price_cents"] for i in quote["items"])
    assert units == [5000, 9000]
    assert quote["subtotal_cents"] == 2 * 9000 + 5000 == 23000
    assert (quote["tax_rate_bps"], quote["tax_cents"]) == (800, 1840)  # staff gets the rate
    assert quote["total_cents"] == 24840
    assert quote["order_status"] == "quote" and quote["quote_number"].startswith("QTE-")
    assert quote["invoice_number"] is None


def test_wholesale_discount_without_stacking(client, users, db) -> None:
    sale_lower = product(db, price=10000, sale=7000)  # sale 7000 < 8500 discounted
    discount_lower = product(db, price=10000, sale=9000)  # 8500 discounted < 9000 sale
    plain = product(db, price=10000)
    quote = create(
        client,
        users["owner_a"],
        [
            {"product_id": sale_lower, "qty": 1},
            {"product_id": discount_lower, "qty": 1},
            {"product_id": plain, "qty": 1},
        ],
        customer_id=customer(db, wholesale=True, bps=1500),
    )
    by_product = {i["product_id"]: i["unit_price_cents"] for i in quote["items"]}
    assert by_product == {sale_lower: 7000, discount_lower: 8500, plain: 8500}
    assert quote["subtotal_cents"] == 24000 and quote["tax_cents"] == 1920


def test_tax_exempt_customer_pays_no_tax(client, users, db) -> None:
    quote = create(
        client,
        users["owner_a"],
        [{"product_id": product(db), "qty": 3}],
        customer_id=customer(db, wholesale=True, bps=1000, exempt=True),
    )
    assert (quote["subtotal_cents"], quote["tax_rate_bps"], quote["tax_cents"]) == (27000, 0, 0)
    assert quote["total_cents"] == 27000


def test_tax_override_is_owner_only_and_rounds_half_up(client, users, db) -> None:
    one_cent = product(db, price=1)
    # 1 cent at 50% = 0.5 -> 1 (half up): subtotal 1, tax 1, total 2.
    quote = create(
        client, users["owner_a"], [{"product_id": one_cent, "qty": 1}], tax_rate_bps=5000
    )
    assert (quote["subtotal_cents"], quote["tax_rate_bps"], quote["tax_cents"]) == (1, 5000, 1)
    assert quote["total_cents"] == 2

    response = client.post(
        INVOICES,
        json={"items": [{"product_id": one_cent, "qty": 1}], "tax_rate_bps": 0},
        headers=users["staff_a"].headers,
    )
    assert response.status_code == 403
    exempt = customer(db, wholesale=True, exempt=True)
    response = client.post(
        INVOICES,
        json={
            "customer_id": exempt,
            "tax_rate_bps": 500,
            "items": [{"product_id": one_cent, "qty": 1}],
        },
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 422 and "tax-exempt" in response.json()["detail"]


def test_walk_in_and_due_dates(client, users, db) -> None:
    walk_in = create(client, users["staff_a"], [{"product_id": product(db), "qty": 1}])
    assert walk_in["customer_name"] == "Walk-in customer" and walk_in["due_date"] is None
    net30 = create(
        client,
        users["owner_a"],
        [{"product_id": product(db), "qty": 1}],
        customer_id=customer(db, wholesale=True, terms="net_30"),
    )
    created = net30["created_at"][:10]
    from datetime import date

    assert date.fromisoformat(net30["due_date"]) - date.fromisoformat(created) == timedelta(30)
    explicit = create(
        client,
        users["owner_a"],
        [{"product_id": product(db), "qty": 1}],
        due_date="2027-01-15",
        notes="Engrave 'A'",
    )
    assert (explicit["due_date"], explicit["notes"]) == ("2027-01-15", "Engrave 'A'")


# ---------------------------------------------------------------------------
# Quote-only edits
# ---------------------------------------------------------------------------
def test_put_items_replaces_and_reprices_a_quote(client, users, db) -> None:
    a, b = product(db, price=1000), product(db, price=2500)
    quote = create(client, users["owner_a"], [{"product_id": a, "qty": 1}], tax_rate_bps=1000)
    response = client.put(
        f"{INVOICES}/{quote['id']}/items",
        json={"items": [{"product_id": b, "qty": 2}, {"product_id": a, "qty": 1}]},
        headers=users["staff_a"].headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert sorted((i["product_id"], i["qty"]) for i in body["items"]) == sorted([(b, 2), (a, 1)])
    assert body["subtotal_cents"] == 6000
    assert (body["tax_rate_bps"], body["tax_cents"], body["total_cents"]) == (1000, 600, 6600)


def test_put_items_on_a_paid_invoice_is_409(client, users, db) -> None:
    p = product(db)
    paid = create(client, users["owner_a"], [{"product_id": p, "qty": 1}], pay_now=True)
    response = client.put(
        f"{INVOICES}/{paid['id']}/items",
        json={"items": [{"product_id": p, "qty": 5}]},
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 409
    assert response.json() == {"detail": "only quotes can be edited"}
    again = client.get(f"{INVOICES}/{paid['id']}", headers=users["owner_a"].headers).json()
    assert [i["qty"] for i in again["items"]] == [1]


# ---------------------------------------------------------------------------
# Status moves, stock and numbers
# ---------------------------------------------------------------------------
def test_skip_to_shipped_decrements_stock_once(client, users, db) -> None:
    p = product(db, stock=10)
    quote = create(
        client, users["staff_a"], [{"product_id": p, "qty": 3}, {"product_id": p, "qty": 1}]
    )
    url = f"{INVOICES}/{quote['id']}/status"
    shipped = client.post(url, json={"status": "shipped"}, headers=users["staff_a"].headers)
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["invoice_number"].startswith("INV-") and shipped.json()["paid_at"]
    assert stock(db, p) == 6
    delivered = client.post(url, json={"status": "delivered"}, headers=users["staff_a"].headers)
    assert delivered.status_code == 200 and stock(db, p) == 6
    backward = client.post(url, json={"status": "paid"}, headers=users["staff_a"].headers)
    assert backward.status_code == 422
    assert backward.json()["detail"] == "order_status cannot move backward (delivered -> paid)"


def test_insufficient_stock_keeps_a_quote_and_burns_no_number(client, users, db) -> None:
    p = product(db, stock=1)
    quote = create(client, users["owner_a"], [{"product_id": p, "qty": 2}])
    before = counters(db)
    response = client.post(
        f"{INVOICES}/{quote['id']}/status",
        json={"status": "paid"},
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 409
    sku = db.execute("select sku from public.products where id = %s", (p,)).fetchone()[0]
    assert (
        response.json()["detail"] == f"insufficient stock for SKU {sku}: 1 available, 2 requested"
    )
    after = client.get(f"{INVOICES}/{quote['id']}", headers=users["owner_a"].headers).json()
    assert (after["order_status"], after["invoice_number"]) == ("quote", None)
    assert counters(db) == before and stock(db, p) == 1


def test_pay_now_is_atomic(client, users, db) -> None:
    p = product(db, stock=5)
    cust = customer(db)
    paid = create(
        client, users["staff_a"], [{"product_id": p, "qty": 2}], customer_id=cust, pay_now=True
    )
    assert paid["order_status"] == "paid" and paid["invoice_number"].startswith("INV-")
    assert stock(db, p) == 3

    before = counters(db)
    response = client.post(
        INVOICES,
        json={"customer_id": cust, "pay_now": True, "items": [{"product_id": p, "qty": 4}]},
        headers=users["staff_a"].headers,
    )
    assert response.status_code == 409 and "insufficient stock" in response.json()["detail"]
    assert (
        db.execute(
            "select count(*) from public.invoices where customer_id = %s", (cust,)
        ).fetchone()[0]
        == 1
    )  # only the first one
    assert counters(db) == before and stock(db, p) == 3


# ---------------------------------------------------------------------------
# Refund, cancel, delete
# ---------------------------------------------------------------------------
def test_refund_and_cancel_rules(client, users, db) -> None:
    owner, staff = users["owner_a"], users["staff_a"]
    p = product(db)
    quote = create(client, owner, [{"product_id": p, "qty": 1}])
    refund_quote = client.post(f"{INVOICES}/{quote['id']}/refund", headers=owner.headers)
    assert refund_quote.status_code == 409
    assert refund_quote.json() == {"detail": "only paid invoices can be refunded"}

    paid = create(client, owner, [{"product_id": p, "qty": 1}], pay_now=True)
    url = f"{INVOICES}/{paid['id']}"
    assert client.post(f"{url}/refund", headers=staff.headers).status_code == 403
    refunded = client.post(f"{url}/refund", headers=owner.headers)
    assert refunded.status_code == 200 and refunded.json()["refunded_at"]
    assert client.post(f"{url}/refund", headers=owner.headers).status_code == 409
    assert stock(db, p) == 99  # no restock in v1

    assert client.post(f"{url}/cancel", headers=staff.headers).status_code == 403
    assert client.post(f"{url}/cancel", headers=owner.headers).status_code == 200
    again = client.post(f"{url}/cancel", headers=owner.headers)
    assert again.status_code == 409 and again.json() == {
        "detail": "this invoice is already cancelled"
    }


def test_delete_quote_only(client, users, db) -> None:
    owner = users["owner_a"]
    p = product(db)
    quote = create(client, owner, [{"product_id": p, "qty": 1}, {"product_id": p, "qty": 2}])
    assert client.delete(f"{INVOICES}/{quote['id']}", headers=owner.headers).status_code == 204
    assert (
        db.execute(
            "select count(*) from public.invoice_line_items where invoice_id = %s", (quote["id"],)
        ).fetchone()[0]
        == 0
    )

    paid = create(client, owner, [{"product_id": p, "qty": 1}], pay_now=True)
    response = client.delete(f"{INVOICES}/{paid['id']}", headers=owner.headers)
    assert response.status_code == 403
    assert response.json() == {
        "detail": "only quotes can be deleted; cancel or refund this invoice instead"
    }
    assert client.get(f"{INVOICES}/{paid['id']}", headers=owner.headers).status_code == 200


# ---------------------------------------------------------------------------
# List filters and CSV
# ---------------------------------------------------------------------------
def test_list_filters(client, users, db) -> None:
    owner = users["owner_a"]
    cust = customer(db)
    p = product(db)
    quote = create(client, owner, [{"product_id": p, "qty": 1}], customer_id=cust)
    paid = create(client, owner, [{"product_id": p, "qty": 1}], customer_id=cust, pay_now=True)

    def ids(**params) -> set[str]:
        response = client.get(INVOICES, params={"limit": 100, **params}, headers=owner.headers)
        assert response.status_code == 200, response.text
        return {i["id"] for i in response.json()}

    assert ids(customer_id=cust) == {quote["id"], paid["id"]}
    assert ids(customer_id=cust, status="paid") == {paid["id"]}
    assert ids(q=quote["quote_number"]) == {quote["id"]}
    assert ids(q=paid["invoice_number"]) == {paid["id"]}
    today = paid["created_at"][:10]
    assert {quote["id"], paid["id"]} <= ids(date_from=today, date_to=today)
    assert not ids(customer_id=cust, date_to="2000-01-01")
    bad = client.get(
        INVOICES, params={"date_from": "2026-10-10", "date_to": "2026-10-01"}, headers=owner.headers
    )
    assert bad.status_code == 422


def test_csv_export(client, users, db) -> None:
    owner = users["owner_a"]
    cust = customer(db)
    # A customer name that a spreadsheet would execute as a formula.
    db.execute("update public.customers set first_name = '=IT-HYPERLINK(1)' where id = %s", (cust,))
    p = product(db, price=1999)
    paid = create(client, owner, [{"product_id": p, "qty": 1}], customer_id=cust, pay_now=True)
    try:
        response = client.get(
            f"{INVOICES}/export.csv",
            params={"customer_id": cust, "status": "paid"},
            headers=users["cpa_a"].headers,
        )
        assert response.status_code == 200
        assert 'filename="invoices.csv"' in response.headers["content-disposition"]
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 1
        row = rows[0]
        assert row["invoice_number"] == paid["invoice_number"]
        assert row["customer_name"] == "'=IT-HYPERLINK(1) Tester"
        assert (row["subtotal"], row["tax"], row["total"]) == ("19.99", "1.60", "21.59")
        # The tenant's currency, as the column just before the first amount.
        assert row["currency"] == "USD"
        header = list(row.keys())
        assert header.index("currency") == header.index("subtotal") - 1
        assert "items" not in row and "description" not in row
    finally:
        db.execute("delete from public.invoices where customer_id = %s", (cust,))
        db.execute("delete from public.customers where id = %s", (cust,))


def test_csv_export_currency_is_the_tenants(client, users, db) -> None:
    db.execute(
        "update public.company_settings set currency = 'CAD' where tenant_id = %s", (TENANT_B,)
    )
    try:
        b = list(
            csv.DictReader(
                io.StringIO(
                    client.get(f"{INVOICES}/export.csv", headers=users["owner_b"].headers).text
                )
            )
        )
        assert b and {r["currency"] for r in b} == {"CAD"}
        a = list(
            csv.DictReader(
                io.StringIO(
                    client.get(f"{INVOICES}/export.csv", headers=users["cpa_a"].headers).text
                )
            )
        )
        assert a and {r["currency"] for r in a} == {"USD"}
    finally:
        db.execute(
            "update public.company_settings set currency = 'USD' where tenant_id = %s",
            (TENANT_B,),
        )
