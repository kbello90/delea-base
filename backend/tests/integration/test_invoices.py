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

from .support import TENANT_A, TENANT_B, TestUser, assert_access, force_currency

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

    paid = create(client, owner, body["items"], pay_now=True, payment_method="card")
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
    paid = create(
        client, users["owner_a"], [{"product_id": p, "qty": 1}], pay_now=True, payment_method="card"
    )
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
    shipped = client.post(
        url, json={"status": "shipped", "payment_method": "card"}, headers=users["staff_a"].headers
    )
    assert shipped.status_code == 200, shipped.text
    assert shipped.json()["invoice_number"].startswith("INV-") and shipped.json()["paid_at"]
    assert stock(db, p) == 6
    delivered = client.post(url, json={"status": "delivered"}, headers=users["staff_a"].headers)
    assert delivered.status_code == 200 and stock(db, p) == 6
    backward = client.post(url, json={"status": "paid"}, headers=users["staff_a"].headers)
    assert backward.status_code == 422
    assert backward.json()["detail"] == "This invoice can't move back to paid."


def test_insufficient_stock_keeps_a_quote_and_burns_no_number(client, users, db) -> None:
    p = product(db, stock=1)
    quote = create(client, users["owner_a"], [{"product_id": p, "qty": 2}])
    before = counters(db)
    response = client.post(
        f"{INVOICES}/{quote['id']}/status",
        json={"status": "paid", "payment_method": "card"},
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 409
    sku = db.execute("select sku from public.products where id = %s", (p,)).fetchone()[0]
    assert response.json()["detail"] == f"Not enough stock for SKU {sku}: 1 available, 2 needed."
    after = client.get(f"{INVOICES}/{quote['id']}", headers=users["owner_a"].headers).json()
    assert (after["order_status"], after["invoice_number"]) == ("quote", None)
    assert counters(db) == before and stock(db, p) == 1


def test_pay_now_is_atomic(client, users, db) -> None:
    p = product(db, stock=5)
    cust = customer(db)
    paid = create(
        client,
        users["staff_a"],
        [{"product_id": p, "qty": 2}],
        customer_id=cust,
        pay_now=True,
        payment_method="cash",
    )
    assert paid["order_status"] == "paid" and paid["invoice_number"].startswith("INV-")
    assert stock(db, p) == 3

    before = counters(db)
    response = client.post(
        INVOICES,
        json={
            "customer_id": cust,
            "pay_now": True,
            "payment_method": "card",
            "items": [{"product_id": p, "qty": 4}],
        },
        headers=users["staff_a"].headers,
    )
    assert response.status_code == 409 and "Not enough stock" in response.json()["detail"]
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

    paid = create(client, owner, [{"product_id": p, "qty": 1}], pay_now=True, payment_method="card")
    url = f"{INVOICES}/{paid['id']}"
    assert client.post(f"{url}/refund", headers=staff.headers).status_code == 403
    refunded = client.post(f"{url}/refund", headers=owner.headers)
    assert refunded.status_code == 200 and refunded.json()["refunded_at"]
    assert client.post(f"{url}/refund", headers=owner.headers).status_code == 409
    assert stock(db, p) == 99  # no restock in v1

    assert client.post(f"{url}/cancel", headers=staff.headers).status_code == 403

    other = create(
        client, owner, [{"product_id": p, "qty": 1}], pay_now=True, payment_method="card"
    )
    other_url = f"{INVOICES}/{other['id']}"
    assert client.post(f"{other_url}/cancel", headers=owner.headers).status_code == 200
    again = client.post(f"{other_url}/cancel", headers=owner.headers)
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

    paid = create(client, owner, [{"product_id": p, "qty": 1}], pay_now=True, payment_method="card")
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
    paid = create(
        client,
        owner,
        [{"product_id": p, "qty": 1}],
        customer_id=cust,
        pay_now=True,
        payment_method="card",
    )

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


def header_after(row: dict, column: str) -> str:
    header = list(row.keys())
    return header[header.index(column) + 1]


def test_csv_export(client, users, db) -> None:
    owner = users["owner_a"]
    cust = customer(db)
    # A customer name that a spreadsheet would execute as a formula.
    db.execute("update public.customers set first_name = '=IT-HYPERLINK(1)' where id = %s", (cust,))
    p = product(db, price=1999)
    paid = create(
        client,
        owner,
        [{"product_id": p, "qty": 1}],
        customer_id=cust,
        pay_now=True,
        payment_method="card",
    )
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
        assert row["payment_method"] == "card"
        assert header_after(row, "paid_at") == "payment_method"
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
    force_currency(db, TENANT_B, "CAD")
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
        force_currency(db, TENANT_B, "USD")


# ---------------------------------------------------------------------------
# Preview: the same numbers as create, and nothing written
# ---------------------------------------------------------------------------
def footprint(db) -> tuple:
    """Everything a create or a sale would touch in tenant A."""
    return db.execute(
        "select (select count(*) from public.invoices where tenant_id = %(t)s), "
        "(select count(*) from public.invoice_line_items where tenant_id = %(t)s), "
        "(select count(*) from public.automation_events where tenant_id = %(t)s), "
        "(select sum(stock_qty) from public.products where tenant_id = %(t)s), "
        "(select next_quote_number from public.company_settings where tenant_id = %(t)s), "
        "(select next_invoice_number from public.company_settings where tenant_id = %(t)s)",
        {"t": TENANT_A},
    ).fetchone()


PRICED = ("subtotal_cents", "tax_rate_bps", "tax_cents", "total_cents")
LINE = ("product_id", "description", "qty", "unit_price_cents", "line_total_cents")


def preview(client, user: TestUser, status_code=200, **body) -> dict:
    response = client.post(f"{INVOICES}/preview", json=body, headers=user.headers)
    assert response.status_code == status_code, response.text
    return response.json()


@pytest.mark.parametrize(
    "case",
    ["retail_on_sale", "wholesale_minimum", "tax_exempt", "owner_override", "walk_in"],
)
def test_preview_matches_create_exactly_and_writes_nothing(client, users, db, case: str) -> None:
    owner, staff = users["owner_a"], users["staff_a"]
    a = product(db, price=10000, sale=9000)
    b = product(db, price=333)
    body: dict = {"items": [{"product_id": a, "qty": 1}, {"product_id": b, "qty": 3}]}
    user = staff
    if case == "retail_on_sale":
        body["customer_id"] = customer(db)
    elif case == "wholesale_minimum":
        body["customer_id"] = customer(db, wholesale=True, bps=1500)
    elif case == "tax_exempt":
        body["customer_id"] = customer(db, wholesale=True, bps=500, exempt=True)
    elif case == "owner_override":
        body["tax_rate_bps"], user = 625, owner

    before = footprint(db)
    priced = preview(client, user, **body)
    assert footprint(db) == before  # no invoice, line, event, stock change or number

    created = create(client, user, body.pop("items"), **body)
    assert {k: priced[k] for k in PRICED} == {k: created[k] for k in PRICED}
    # Stored lines are read back in id order, so compare them as sets of rows.
    assert sorted(tuple(ln[k] for k in LINE) for ln in priced["lines"]) == sorted(
        tuple(ln[k] for k in LINE) for ln in created["items"]
    )
    assert priced["stock_shortages"] == []


def test_preview_rules(client, users, db) -> None:
    owner, staff, cpa = users["owner_a"], users["staff_a"], users["cpa_a"]
    p = product(db, stock=2)
    items = [{"product_id": p, "qty": 2}, {"product_id": p, "qty": 1}]
    before = footprint(db)
    # Quantities are summed per product, as the database checks when paying.
    sku = db.execute("select sku from public.products where id = %s", (p,)).fetchone()[0]
    assert preview(client, staff, items=items)["stock_shortages"] == [
        {"product_id": p, "sku": sku, "available": 2, "needed": 3}
    ]
    detail = preview(client, staff, 403, items=items, tax_rate_bps=0)["detail"]
    assert detail == "only the owner can override the tax rate"
    refused = client.post(f"{INVOICES}/preview", json={"items": items}, headers=cpa.headers)
    assert refused.status_code == 403
    preview(client, owner, 404, items=[{"product_id": SEED_B_PRODUCT, "qty": 1}])
    preview(client, owner, 404, items=items, customer_id=SEED_B_CUSTOMER)
    for forged in ({"pay_now": True}, {"total_cents": 1}, {"notes": "x"}):
        preview(client, owner, 422, items=items, **forged)
    assert footprint(db) == before


def test_items_preview_uses_the_quotes_customer_and_stored_rate(client, users, db) -> None:
    owner, staff = users["owner_a"], users["staff_a"]
    a, b = product(db, price=1000), product(db, price=2500)
    quote = create(
        client,
        owner,
        [{"product_id": a, "qty": 1}],
        customer_id=customer(db, wholesale=True, bps=1000),
        tax_rate_bps=1000,
    )
    url = f"{INVOICES}/{quote['id']}/items"
    items = [{"product_id": b, "qty": 2}, {"product_id": a, "qty": 1}]
    before = footprint(db)
    priced = client.post(f"{url}/preview", json={"items": items}, headers=staff.headers)
    assert priced.status_code == 200, priced.text
    assert footprint(db) == before
    replaced = client.put(url, json={"items": items}, headers=staff.headers).json()
    assert {k: priced.json()[k] for k in PRICED} == {k: replaced[k] for k in PRICED}
    assert priced.json()["tax_rate_bps"] == 1000  # the owner's override stays in force

    paid = create(client, owner, [{"product_id": a, "qty": 1}], pay_now=True, payment_method="card")
    refused = client.post(
        f"{INVOICES}/{paid['id']}/items/preview", json={"items": items}, headers=owner.headers
    )
    assert refused.status_code == 409 and refused.json() == {"detail": "only quotes can be edited"}
    for name, expected in (("cpa_a", 403), ("owner_b", 404)):
        response = client.post(f"{url}/preview", json={"items": items}, headers=users[name].headers)
        assert response.status_code == expected, name


# ---------------------------------------------------------------------------
# Payment method
# ---------------------------------------------------------------------------
def method_of(db, invoice_id: str) -> str | None:
    return db.execute(
        "select payment_method::text from public.invoices where id = %s", (invoice_id,)
    ).fetchone()[0]


def test_payment_method_is_required_when_paying_and_refused_otherwise(client, users, db) -> None:
    staff = users["staff_a"]
    p = product(db, stock=10)
    line = [{"product_id": p, "qty": 1}]
    before = footprint(db)
    response = client.post(INVOICES, json={"items": line, "pay_now": True}, headers=staff.headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "a payment method is required to take payment"}
    response = client.post(
        INVOICES, json={"items": line, "payment_method": "cash"}, headers=staff.headers
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "the payment method is recorded only when a quote is paid"}
    bad = client.post(
        INVOICES,
        json={"items": line, "pay_now": True, "payment_method": "cheque"},
        headers=staff.headers,
    )
    assert bad.status_code == 422
    assert footprint(db) == before  # nothing created, no number used

    sale = create(client, staff, line, pay_now=True, payment_method="cash")
    assert sale["payment_method"] == "cash" and method_of(db, sale["id"]) == "cash"

    quote = create(client, staff, line)
    assert quote["payment_method"] is None
    url = f"{INVOICES}/{quote['id']}/status"
    missing = client.post(url, json={"status": "paid"}, headers=staff.headers)
    assert missing.status_code == 422
    current = client.get(f"{INVOICES}/{quote['id']}", headers=staff.headers).json()
    assert current["order_status"] == "quote"
    paid = client.post(
        url, json={"status": "paid", "payment_method": "bank_transfer"}, headers=staff.headers
    )
    assert paid.status_code == 200 and paid.json()["payment_method"] == "bank_transfer"
    later = client.post(
        url, json={"status": "shipped", "payment_method": "cash"}, headers=staff.headers
    )
    assert later.status_code == 422
    moved = client.post(url, json={"status": "shipped"}, headers=staff.headers)
    assert moved.status_code == 200 and moved.json()["payment_method"] == "bank_transfer"


def test_only_the_owner_corrects_a_payment_method(client, users, db) -> None:
    owner, staff, cpa = users["owner_a"], users["staff_a"], users["cpa_a"]
    p = product(db)
    sale = create(client, staff, [{"product_id": p, "qty": 1}], pay_now=True, payment_method="cash")
    url = f"{INVOICES}/{sale['id']}"
    refused = client.patch(url, json={"payment_method": "card"}, headers=staff.headers)
    assert refused.status_code == 403
    assert refused.json() == {"detail": "only the owner can change the payment method"}
    by_cpa = client.patch(url, json={"payment_method": "card"}, headers=cpa.headers)
    assert by_cpa.status_code == 403
    assert method_of(db, sale["id"]) == "cash"
    fixed = client.patch(url, json={"payment_method": "card"}, headers=owner.headers)
    assert fixed.status_code == 200 and fixed.json()["payment_method"] == "card"
    cleared = client.patch(url, json={"payment_method": None}, headers=owner.headers)
    assert cleared.status_code == 422 and method_of(db, sale["id"]) == "card"

    quote = create(client, owner, [{"product_id": p, "qty": 1}])
    on_quote = client.patch(
        f"{INVOICES}/{quote['id']}", json={"payment_method": "card"}, headers=owner.headers
    )
    assert on_quote.status_code == 409
    assert on_quote.json() == {
        "detail": "A quote has no payment method; it is recorded when it is paid."
    }


# ---------------------------------------------------------------------------
# Cancelled and refunded invoices
# ---------------------------------------------------------------------------
def snapshot(db, invoice_id: str) -> tuple:
    return db.execute(
        "select order_status::text, refunded_at, cancelled_at, updated_at "
        "from public.invoices where id = %s",
        (invoice_id,),
    ).fetchone()


CANCELLED = {"detail": "This invoice is cancelled, so its status can't change."}
REFUNDED = {"detail": "This invoice is refunded, so its status can't change."}


def test_cancelled_and_refunded_invoices_are_closed(client, users, db) -> None:
    owner, staff = users["owner_a"], users["staff_a"]
    p = product(db)
    line = [{"product_id": p, "qty": 1}]
    cancelled = create(client, owner, line, pay_now=True, payment_method="card")
    refunded = create(client, owner, line, pay_now=True, payment_method="card")
    c_url, r_url = f"{INVOICES}/{cancelled['id']}", f"{INVOICES}/{refunded['id']}"
    assert client.post(f"{c_url}/cancel", headers=owner.headers).status_code == 200
    assert client.post(f"{r_url}/refund", headers=owner.headers).status_code == 200
    before = (snapshot(db, cancelled["id"]), snapshot(db, refunded["id"]))

    for user in (owner, staff):
        for target in ("in_production", "shipped", "delivered"):
            moved = client.post(f"{c_url}/status", json={"status": target}, headers=user.headers)
            assert moved.status_code == 409 and moved.json() == CANCELLED
            moved = client.post(f"{r_url}/status", json={"status": target}, headers=user.headers)
            assert moved.status_code == 409 and moved.json() == REFUNDED

    refund = client.post(f"{c_url}/refund", headers=owner.headers)
    assert refund.status_code == 409
    assert refund.json() == {"detail": "A cancelled invoice can't be refunded."}
    cancel = client.post(f"{r_url}/cancel", headers=owner.headers)
    assert cancel.status_code == 409
    assert cancel.json() == {"detail": "A refunded invoice can't be cancelled."}
    assert (snapshot(db, cancelled["id"]), snapshot(db, refunded["id"])) == before

    # A cancelled quote can't be paid either (and no number or stock is used).
    quote = create(client, owner, line)
    assert client.post(f"{INVOICES}/{quote['id']}/cancel", headers=owner.headers).status_code == 200
    stock_before, numbers_before = stock(db, p), counters(db)
    pay = client.post(
        f"{INVOICES}/{quote['id']}/status",
        json={"status": "paid", "payment_method": "cash"},
        headers=staff.headers,
    )
    assert pay.status_code == 409 and pay.json() == CANCELLED
    assert (stock(db, p), counters(db)) == (stock_before, numbers_before)


# ---------------------------------------------------------------------------
# Notes and due date
# ---------------------------------------------------------------------------
def test_notes_at_any_status_due_date_on_quotes_only(client, users, db) -> None:
    owner, staff, cpa = users["owner_a"], users["staff_a"], users["cpa_a"]
    p = product(db)
    quote = create(client, owner, [{"product_id": p, "qty": 1}], notes="first")
    q_url = f"{INVOICES}/{quote['id']}"
    edited = client.patch(
        q_url, json={"notes": "Engrave 'M'", "due_date": "2027-02-01"}, headers=staff.headers
    )
    assert edited.status_code == 200, edited.text
    assert (edited.json()["notes"], edited.json()["due_date"]) == ("Engrave 'M'", "2027-02-01")
    assert edited.json()["total_cents"] == quote["total_cents"]
    cleared = client.patch(q_url, json={"notes": None, "due_date": None}, headers=owner.headers)
    assert (cleared.json()["notes"], cleared.json()["due_date"]) == (None, None)

    sale = create(client, owner, [{"product_id": p, "qty": 1}], pay_now=True, payment_method="card")
    s_url = f"{INVOICES}/{sale['id']}"
    assert client.post(f"{s_url}/cancel", headers=owner.headers).status_code == 200
    noted = client.patch(s_url, json={"notes": "Changed their mind"}, headers=staff.headers)
    assert noted.status_code == 200 and noted.json()["notes"] == "Changed their mind"
    due = client.patch(s_url, json={"due_date": "2027-02-01"}, headers=owner.headers)
    assert due.status_code == 409
    assert due.json() == {"detail": "The due date can only change while the invoice is a quote."}
    assert client.get(s_url, headers=owner.headers).json()["due_date"] == sale["due_date"]

    assert client.patch(q_url, json={"notes": "x"}, headers=cpa.headers).status_code == 403
    assert client.patch(q_url, json={}, headers=owner.headers).status_code == 422
    for forged in (
        {"total_cents": 1},
        {"order_status": "paid"},
        {"customer_id": customer(db)},
        {"tax_rate_bps": 0},
        {"tenant_id": TENANT_B},
    ):
        assert client.patch(q_url, json=forged, headers=owner.headers).status_code == 422
    other_tenant = client.patch(
        f"{INVOICES}/{SEED_B_INVOICE}", json={"notes": "x"}, headers=owner.headers
    )
    assert other_tenant.status_code == 404


def test_new_endpoints_role_matrix(client, users, db) -> None:
    owner = users["owner_a"]
    p = product(db)
    quote = create(client, owner, [{"product_id": p, "qty": 1}])
    items = {"items": [{"product_id": p, "qty": 1}]}
    writers = {"owner_a": 200, "staff_a": 200, "owner_b": 404}
    assert_access(client, users, "POST", f"{INVOICES}/preview", writers, json=items)
    assert_access(
        client, users, "POST", f"{INVOICES}/{quote['id']}/items/preview", writers, json=items
    )
    assert_access(
        client, users, "PATCH", f"{INVOICES}/{quote['id']}", writers, json={"notes": "matrix"}
    )


# ---------------------------------------------------------------------------
# Lines keep their entry order
# ---------------------------------------------------------------------------
def test_lines_come_back_in_the_order_sent(client, users, db) -> None:
    owner = users["owner_a"]
    # Created in an order that is neither id order nor name order.
    c, a, b = product(db), product(db), product(db)
    sent = [{"product_id": c, "qty": 1}, {"product_id": a, "qty": 2}, {"product_id": b, "qty": 3}]
    quote = create(client, owner, sent)
    as_sent = [(i["product_id"], i["qty"]) for i in sent]
    assert [(i["product_id"], i["qty"]) for i in quote["items"]] == as_sent
    url = f"{INVOICES}/{quote['id']}"
    again = client.get(url, headers=owner.headers).json()
    assert [(i["product_id"], i["qty"]) for i in again["items"]] == as_sent

    # Replacing the lines (the same product twice included) keeps the new order.
    replacement = [
        {"product_id": b, "qty": 1},
        {"product_id": c, "qty": 4},
        {"product_id": b, "qty": 2},
    ]
    replaced = client.put(f"{url}/items", json={"items": replacement}, headers=owner.headers).json()
    as_replaced = [(i["product_id"], i["qty"]) for i in replacement]
    assert [(i["product_id"], i["qty"]) for i in replaced["items"]] == as_replaced
    again = client.get(url, headers=owner.headers).json()
    assert [(i["product_id"], i["qty"]) for i in again["items"]] == as_replaced
    positions = db.execute(
        "select position from public.invoice_line_items where invoice_id = %s order by position",
        (quote["id"],),
    ).fetchall()
    assert [p for (p,) in positions] == [1, 2, 3]


def test_made_to_order_products_are_never_short_and_keep_their_stock(client, users, db) -> None:
    """Made after the sale (migration 20261027000100): no shortage in the preview, and paying
    neither checks nor decrements their stock. A stocked product next to it still is."""
    staff = users["staff_a"]
    made = product(db, stock=0)
    stocked = product(db, stock=5)
    db.execute("update public.products set made_to_order = true where id = %s", (made,))
    items = [{"product_id": made, "qty": 3}, {"product_id": stocked, "qty": 2}]
    assert preview(client, staff, items=items)["stock_shortages"] == []

    sale = create(client, staff, items, pay_now=True, payment_method="cash")
    assert sale["order_status"] == "paid"
    stock = dict(
        db.execute(
            "select id::text, stock_qty from public.products where id = any(%s::uuid[])",
            ([made, stocked],),
        ).fetchall()
    )
    assert stock == {made: 0, stocked: 3}
