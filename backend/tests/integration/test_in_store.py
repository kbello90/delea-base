"""Selling in the shop (Slice C1): in-store sales and deposits, personalization answers on
admin-made lines (checked as on the website), barcode lookup by exact SKU, and the In store
source filter.

Each test makes its own IT- products and customers; their invoices are removed afterwards
(conftest). Every invoice a test asks for has an IT- customer, even one expected to be refused,
so a deliberate break that lets it through still leaves nothing in the seed tenant."""

import uuid

import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TestUser

pytestmark = pytest.mark.integration

INVOICES = "/api/v1/invoices"
PRODUCTS = "/api/v1/products"


def tag() -> str:
    return uuid.uuid4().hex[:6].upper()


def product(db, price=10000, stock=10, sku: str | None = None) -> str:
    return str(
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty) "
            "values (%s, %s, %s, 'other', %s, %s) returning id",
            (TENANT_A, sku or f"IT-{tag()}", f"IT ring {tag()}", price, stock),
        ).fetchone()[0]
    )


def ring_with_fields(db) -> tuple[str, str, str]:
    """A product asking for an Initial (text, at most 3, required) and a Size (6, 7 or 8,
    optional). Returns (product, initial field, size field)."""
    p = product(db)
    initial = db.execute(
        "insert into public.product_personalization_fields "
        "(tenant_id, product_id, position, label, kind, required, max_length) "
        "values (%s, %s, 1, 'Initial', 'text', true, 3) returning id",
        (TENANT_A, p),
    ).fetchone()[0]
    size = db.execute(
        "insert into public.product_personalization_fields "
        "(tenant_id, product_id, position, label, kind, required, choices) "
        "values (%s, %s, 2, 'Size', 'choice', false, '{6,7,8}') returning id",
        (TENANT_A, p),
    ).fetchone()[0]
    return p, str(initial), str(size)


def customer(db) -> str:
    return str(
        db.execute(
            "insert into public.customers (tenant_id, first_name, last_name) "
            "values (%s, %s, 'Tester') returning id",
            (TENANT_A, f"IT-{tag()}"),
        ).fetchone()[0]
    )


def stock(db, product_id: str) -> int:
    return db.execute(
        "select stock_qty from public.products where id = %s", (product_id,)
    ).fetchone()[0]


def invoices_for(db, customer_id: str) -> int:
    return db.execute(
        "select count(*) from public.invoices where customer_id = %s", (customer_id,)
    ).fetchone()[0]


def create(client, user: TestUser, body: dict, code=201) -> dict:
    response = client.post(INVOICES, json=body, headers=user.headers)
    assert response.status_code == code, response.text
    return response.json()


def ledger(invoice: dict) -> list[tuple]:
    return [(p["kind"], p["method"], p["status"], p["amount_cents"]) for p in invoice["payments"]]


# ---------------------------------------------------------------------------
# A sale at the counter
# ---------------------------------------------------------------------------
def test_an_in_store_sale_keeps_each_lines_answers(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    p, initial, size = ring_with_fields(db)
    plain = product(db, price=5000)
    sale = create(
        client,
        users["staff_a"],
        {
            "customer_id": customer(db),
            "source": "in_store",
            "pay_now": True,
            "payment_method": "cash",
            "items": [
                {"product_id": p, "qty": 1, "personalization": {initial: " KB ", size: "7"}},
                {"product_id": p, "qty": 1, "personalization": {initial: "M"}},
                {"product_id": plain, "qty": 2},
            ],
        },
    )
    assert (sale["source"], sale["order_status"]) == ("in_store", "paid")
    assert [line["personalization"] for line in sale["items"]] == [
        [{"label": "Initial", "value": "KB"}, {"label": "Size", "value": "7"}],
        [{"label": "Initial", "value": "M"}],  # the optional Size left blank
        None,
    ]
    assert ledger(sale) == [("payment", "cash", "succeeded", sale["total_cents"])]
    assert (stock(db, p), stock(db, plain)) == (8, 8)  # both ring lines count against stock


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({}, "Initial is required"),
        ({"initial": "   "}, "Initial is required"),  # blank counts as not answered
        ({"initial": "ABCD"}, "Initial is too long"),
        ({"initial": "A\tB"}, "Initial has characters that can't be used"),
        ({"initial": "A", "size": "9"}, "Size must be one of its choices"),
        ({"initial": "A", "other": "x"}, "don't match the product's personalization"),
    ],
)
def test_answers_are_checked_as_on_the_website(
    client: TestClient, users: dict[str, TestUser], db, answers: dict, message: str
) -> None:
    p, initial, size = ring_with_fields(db)
    other_product, other_field, _ = ring_with_fields(db)  # a field of another product
    ids = {"initial": initial, "size": size, "other": other_field}
    buyer = customer(db)
    response = client.post(
        INVOICES,
        json={
            "customer_id": buyer,
            "source": "in_store",
            "pay_now": True,
            "payment_method": "card",
            "items": [
                {
                    "product_id": p,
                    "qty": 1,
                    "personalization": {ids[k]: v for k, v in answers.items()},
                }
            ],
        },
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]
    assert response.json()["detail"].startswith("IT ring ")  # names the product
    assert invoices_for(db, buyer) == 0  # nothing kept
    assert stock(db, p) == 10


def test_answers_for_a_product_without_fields_are_refused(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    plain = product(db)
    _, field, _ = ring_with_fields(db)
    buyer = customer(db)  # IT-: cleanup finds the row even if a break lets it through
    create(
        client,
        users["owner_a"],
        {
            "customer_id": buyer,
            "items": [{"product_id": plain, "qty": 1, "personalization": {field: "A"}}],
        },
        code=422,
    )
    assert invoices_for(db, buyer) == 0


def test_previews_ignore_answers(client: TestClient, users: dict[str, TestUser], db) -> None:
    """Answers don't change prices, so pricing a cart while the cashier types works."""
    p, initial, _ = ring_with_fields(db)
    response = client.post(
        f"{INVOICES}/preview",
        json={"items": [{"product_id": p, "qty": 1, "personalization": {initial: ""}}]},
        headers=users["staff_a"].headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["subtotal_cents"] == 10000


# ---------------------------------------------------------------------------
# Deposits
# ---------------------------------------------------------------------------
def test_a_deposit_is_taken_with_the_quote_in_one_step(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    p, initial, _ = ring_with_fields(db)
    deposit = create(
        client,
        users["staff_a"],
        {
            "customer_id": customer(db),
            "source": "in_store",
            "deposit_cents": 3000,
            "payment_method": "card",
            "items": [{"product_id": p, "qty": 1, "personalization": {initial: "JD"}}],
        },
    )
    assert (deposit["source"], deposit["order_status"]) == ("in_store", "quote")
    assert deposit["invoice_number"] is None
    assert (deposit["amount_paid_cents"], deposit["balance_cents"]) == (3000, 7800)
    assert ledger(deposit) == [("payment", "card", "succeeded", 3000)]
    assert stock(db, p) == 10  # stock moves when it is paid in full

    rest = client.post(
        f"{INVOICES}/{deposit['id']}/payments",
        json={"amount_cents": 7800, "method": "cash"},
        headers=users["staff_a"].headers,
    )
    assert rest.status_code == 201, rest.text
    paid = rest.json()
    assert (paid["order_status"], paid["balance_cents"], paid["mixed_methods"]) == (
        "paid",
        0,
        True,
    )
    assert paid["items"][0]["personalization"] == [{"label": "Initial", "value": "JD"}]
    assert stock(db, p) == 9


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"deposit_cents": 10800, "payment_method": "cash"}, "A deposit is less than the total"),
        ({"deposit_cents": 20000, "payment_method": "cash"}, "A deposit is less than the total"),
        ({"deposit_cents": 100, "payment_method": "cash", "pay_now": True}, "not both"),
        ({"deposit_cents": 100}, "a payment method is required"),
        ({"deposit_cents": 0, "payment_method": "cash"}, None),
    ],
)
def test_deposit_refusals_keep_nothing(
    client: TestClient, users: dict[str, TestUser], db, extra: dict, message: str | None
) -> None:
    buyer = customer(db)
    response = client.post(
        INVOICES,
        json={"customer_id": buyer, "items": [{"product_id": product(db), "qty": 1}], **extra},
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 422, response.text
    if message:
        assert message in str(response.json()["detail"])
    assert invoices_for(db, buyer) == 0


# ---------------------------------------------------------------------------
# Editing a quote's lines keeps (and checks) the answers
# ---------------------------------------------------------------------------
def test_replacing_a_quotes_lines_keeps_and_checks_answers(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    p, initial, size = ring_with_fields(db)
    quote = create(
        client,
        users["owner_a"],
        {
            "customer_id": customer(db),
            "source": "in_store",
            "items": [{"product_id": p, "qty": 1, "personalization": {initial: "AB"}}],
        },
    )
    url = f"{INVOICES}/{quote['id']}/items"
    staff = users["staff_a"].headers
    replaced = client.put(
        url,
        json={
            "items": [{"product_id": p, "qty": 2, "personalization": {initial: "AB", size: "8"}}]
        },
        headers=staff,
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["items"][0]["personalization"] == [
        {"label": "Initial", "value": "AB"},
        {"label": "Size", "value": "8"},
    ]

    refused = client.put(url, json={"items": [{"product_id": p, "qty": 1}]}, headers=staff)
    assert refused.status_code == 422, refused.text
    kept = client.get(f"{INVOICES}/{quote['id']}", headers=staff).json()
    assert [(line["qty"], len(line["personalization"])) for line in kept["items"]] == [(2, 2)]


# ---------------------------------------------------------------------------
# Source filter and barcode lookup
# ---------------------------------------------------------------------------
def test_in_store_invoices_have_their_own_source_filter(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    buyer = customer(db)
    p = product(db)
    sale = create(
        client,
        users["owner_a"],
        {
            "customer_id": buyer,
            "source": "in_store",
            "pay_now": True,
            "payment_method": "cash",
            "items": [{"product_id": p, "qty": 1}],
        },
    )
    made = create(
        client, users["owner_a"], {"customer_id": buyer, "items": [{"product_id": p, "qty": 1}]}
    )
    assert made["source"] == "admin"  # quotes made in the admin stay admin

    def listed(source: str) -> set[str]:
        response = client.get(
            INVOICES,
            params={"source": source, "customer_id": buyer},
            headers=users["cpa_a"].headers,
        )
        assert response.status_code == 200, response.text
        return {row["id"] for row in response.json()}

    assert listed("in_store") == {sale["id"]}
    assert listed("admin") == {made["id"]}
    assert listed("storefront") == set()
    # A client can't claim a website order (or anything else) as its source.
    create(
        client,
        users["owner_a"],
        {"customer_id": buyer, "source": "storefront", "items": [{"product_id": p, "qty": 1}]},
        code=422,
    )


def test_a_scan_finds_the_exact_sku_in_any_case(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    code = f"IT-{tag()}"
    p = product(db, sku=code)
    product(db, sku=f"{code}-2")  # the scanned code is a prefix of this one: not a match

    def scan(value: str) -> list[str]:
        response = client.get(PRODUCTS, params={"sku": value}, headers=users["staff_a"].headers)
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()]

    assert scan(code) == [p]
    assert scan(code.lower()) == [p]
    assert scan(f" {code} ") == [p]  # a scanner's stray space
    assert scan(code[:-1]) == []  # a partial code adds nothing
    # Another shop's products are never found.
    other = client.get(PRODUCTS, params={"sku": code}, headers=users["owner_b"].headers)
    assert other.json() == []
