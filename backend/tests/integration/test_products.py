"""Products CRUD through the real app with real tokens: roles, tenant isolation, errors."""

import psycopg
import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, new_sku, product_payload

pytestmark = pytest.mark.integration

PRODUCTS = "/api/v1/products"
SEED_A_PRODUCT = "a1000000-0000-0000-0000-000000000001"  # Custom Initial Necklace
SEED_B_PRODUCT = "b2000000-0000-0000-0000-000000000001"  # Sapphire Pendant


def create(client: TestClient, user: TestUser, **overrides: object) -> dict:
    response = client.post(PRODUCTS, json=product_payload(**overrides), headers=user.headers)
    assert response.status_code == 201, response.text
    return response.json()


def row_count(db: psycopg.Connection, where: str, params: tuple) -> int:
    return db.execute(f"select count(*) from public.products where {where}", params).fetchone()[0]


# ---------------------------------------------------------------------------
# Owner
# ---------------------------------------------------------------------------
def test_owner_full_crud(client: TestClient, users: dict[str, TestUser], db) -> None:
    owner = users["owner_a"]
    product = create(client, owner, name="Crud Ring", price_cents=9900)
    assert product["tenant_id"] == TENANT_A
    assert product["metal"] == "silver_925"
    assert product["price_cents"] == 9900
    product_id = product["id"]

    fetched = client.get(f"{PRODUCTS}/{product_id}", headers=owner.headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Crud Ring"

    updated = client.patch(
        f"{PRODUCTS}/{product_id}",
        json={"name": "Crud Ring v2", "price_cents": 10900, "metal": "platinum", "icon": None},
        headers=owner.headers,
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert (body["name"], body["price_cents"], body["metal"]) == ("Crud Ring v2", 10900, "platinum")
    assert body["updated_at"] >= product["updated_at"]

    deleted = client.delete(f"{PRODUCTS}/{product_id}", headers=owner.headers)
    assert deleted.status_code == 204
    assert client.get(f"{PRODUCTS}/{product_id}", headers=owner.headers).status_code == 404
    assert row_count(db, "id = %s", (product_id,)) == 0


def test_list_is_ordered_filtered_and_paged(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    response = client.get(PRODUCTS, headers=owner.headers)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 6  # the six seeded demo products
    assert [p["sort_order"] for p in items] == sorted(p["sort_order"] for p in items)

    draft = create(client, owner, name="Unpublished Draft", is_published=False, sort_order=0)
    published = client.get(PRODUCTS, params={"is_published": "true"}, headers=owner.headers).json()
    assert draft["id"] not in {p["id"] for p in published}
    drafts = client.get(PRODUCTS, params={"is_published": "false"}, headers=owner.headers).json()
    assert [p["id"] for p in drafts] == [draft["id"]]

    by_name = client.get(PRODUCTS, params={"q": "necklace"}, headers=owner.headers).json()
    assert {p["sku"] for p in by_name} == {"DL-NK-01", "DL-NK-02"}
    by_sku = client.get(PRODUCTS, params={"q": "dl-rg"}, headers=owner.headers).json()
    assert {p["sku"] for p in by_sku} == {"DL-RG-01", "DL-RG-02"}
    # LIKE wildcards in the search term are literal, not patterns
    assert client.get(PRODUCTS, params={"q": "%"}, headers=owner.headers).json() == []

    page1 = client.get(PRODUCTS, params={"limit": 3, "offset": 0}, headers=owner.headers).json()
    page2 = client.get(PRODUCTS, params={"limit": 3, "offset": 3}, headers=owner.headers).json()
    assert len(page1) == 3 and len(page2) == 3
    assert not {p["id"] for p in page1} & {p["id"] for p in page2}

    assert client.get(PRODUCTS, params={"limit": 0}, headers=owner.headers).status_code == 422
    assert client.get(PRODUCTS, params={"limit": 101}, headers=owner.headers).status_code == 422


# ---------------------------------------------------------------------------
# Staff and CPA
# ---------------------------------------------------------------------------
def test_staff_can_read_but_not_write(client: TestClient, users: dict[str, TestUser], db) -> None:
    staff = users["staff_a"]
    assert client.get(PRODUCTS, headers=staff.headers).status_code == 200
    assert client.get(f"{PRODUCTS}/{SEED_A_PRODUCT}", headers=staff.headers).status_code == 200

    sku = new_sku()
    created = client.post(PRODUCTS, json=product_payload(sku=sku), headers=staff.headers)
    assert created.status_code == 403
    assert row_count(db, "sku = %s", (sku,)) == 0

    patched = client.patch(
        f"{PRODUCTS}/{SEED_A_PRODUCT}", json={"name": "Hacked"}, headers=staff.headers
    )
    assert patched.status_code == 403
    deleted = client.delete(f"{PRODUCTS}/{SEED_A_PRODUCT}", headers=staff.headers)
    assert deleted.status_code == 403
    assert row_count(db, "id = %s and name = %s", (SEED_A_PRODUCT, "Custom Initial Necklace")) == 1


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", PRODUCTS, None),
        ("GET", f"{PRODUCTS}/{SEED_A_PRODUCT}", None),
        ("POST", PRODUCTS, "payload"),
        ("PATCH", f"{PRODUCTS}/{SEED_A_PRODUCT}", {"name": "Hacked"}),
        ("DELETE", f"{PRODUCTS}/{SEED_A_PRODUCT}", None),
    ],
)
def test_cpa_is_forbidden_everywhere(
    client: TestClient, users: dict[str, TestUser], method: str, path: str, body
) -> None:
    json_body = product_payload() if body == "payload" else body
    response = client.request(method, path, json=json_body, headers=users["cpa_a"].headers)
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------
def test_tenant_b_owner_cannot_touch_tenant_a_products(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner_b = users["owner_b"]
    target = create(client, users["owner_a"], name="Tenant A Only")["id"]

    for product_id in (target, SEED_A_PRODUCT):
        url = f"{PRODUCTS}/{product_id}"
        assert client.get(url, headers=owner_b.headers).status_code == 404
        assert (
            client.patch(url, json={"name": "Stolen"}, headers=owner_b.headers).status_code == 404
        )
        assert client.delete(url, headers=owner_b.headers).status_code == 404

    assert row_count(db, "id = %s and name = %s", (target, "Tenant A Only")) == 1
    assert row_count(db, "id = %s and name = %s", (SEED_A_PRODUCT, "Custom Initial Necklace")) == 1


def test_lists_never_cross_tenants(client: TestClient, users: dict[str, TestUser]) -> None:
    list_a = client.get(PRODUCTS, params={"limit": 100}, headers=users["owner_a"].headers).json()
    list_b = client.get(PRODUCTS, params={"limit": 100}, headers=users["owner_b"].headers).json()
    assert list_a and list_b
    assert {p["tenant_id"] for p in list_a} == {TENANT_A}
    assert {p["tenant_id"] for p in list_b} == {TENANT_B}
    assert SEED_B_PRODUCT not in {p["id"] for p in list_a}
    assert SEED_A_PRODUCT not in {p["id"] for p in list_b}
    # Search can't reach across tenants either
    assert (
        client.get(PRODUCTS, params={"q": "sapphire"}, headers=users["owner_a"].headers).json()
        == []
    )


def test_tenant_id_in_body_is_never_used(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    owner = users["owner_a"]
    sku = new_sku()
    response = client.post(
        PRODUCTS, json=product_payload(sku=sku, tenant_id=TENANT_B), headers=owner.headers
    )
    assert response.status_code == 422
    assert row_count(db, "sku = %s", (sku,)) == 0

    product = create(client, owner)
    moved = client.patch(
        f"{PRODUCTS}/{product['id']}", json={"tenant_id": TENANT_B}, headers=owner.headers
    )
    assert moved.status_code == 422
    assert row_count(db, "id = %s and tenant_id = %s", (product["id"], TENANT_A)) == 1


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def test_duplicate_sku_is_409(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    sku = new_sku()
    create(client, owner, sku=sku)
    duplicate = client.post(PRODUCTS, json=product_payload(sku=sku), headers=owner.headers)
    assert duplicate.status_code == 409
    assert duplicate.json() == {"detail": "A record with these values already exists"}
    assert "products_tenant_id_sku_key" not in duplicate.text

    # The same SKU is fine in another tenant (uniqueness is per tenant)
    other = client.post(PRODUCTS, json=product_payload(sku=sku), headers=users["owner_b"].headers)
    assert other.status_code == 201


def test_seeded_sku_conflicts_too(client: TestClient, users: dict[str, TestUser]) -> None:
    response = client.post(
        PRODUCTS, json=product_payload(sku="DL-NK-01"), headers=users["owner_a"].headers
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "bad",
    [
        {"price_cents": -1},
        {"price_cents": "12.50"},
        {"price_cents": 2**63},
        {"sale_price_cents": -5},
        {"metal": "bronze"},
    ],
)
def test_bad_values_are_422(client: TestClient, users: dict[str, TestUser], db, bad: dict) -> None:
    sku = new_sku()
    response = client.post(
        PRODUCTS, json=product_payload(sku=sku, **bad), headers=users["owner_a"].headers
    )
    assert response.status_code == 422
    assert row_count(db, "sku = %s", (sku,)) == 0


def test_patch_rejects_bad_values_and_empty_bodies(
    client: TestClient, users: dict[str, TestUser]
) -> None:
    owner = users["owner_a"]
    product = create(client, owner)
    url = f"{PRODUCTS}/{product['id']}"
    assert client.patch(url, json={"price_cents": -1}, headers=owner.headers).status_code == 422
    assert client.patch(url, json={"name": None}, headers=owner.headers).status_code == 422
    assert client.patch(url, json={}, headers=owner.headers).status_code == 422
    unchanged = client.get(url, headers=owner.headers).json()
    assert unchanged["price_cents"] == product["price_cents"]


def test_unknown_product_is_404(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    missing = f"{PRODUCTS}/00000000-0000-0000-0000-000000000000"
    assert client.get(missing, headers=owner.headers).status_code == 404
    assert client.patch(missing, json={"name": "x"}, headers=owner.headers).status_code == 404
    assert client.delete(missing, headers=owner.headers).status_code == 404
    assert client.get(f"{PRODUCTS}/not-a-uuid", headers=owner.headers).status_code == 422


def test_made_to_order_and_lead_time_note(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"].headers
    created = client.post(
        PRODUCTS,
        json=product_payload(made_to_order=True, lead_time_note="  Ships in 2-3 weeks  "),
        headers=owner,
    )
    assert created.status_code == 201, created.text
    product = created.json()
    assert (product["made_to_order"], product["lead_time_note"]) == (True, "Ships in 2-3 weeks")

    url = f"{PRODUCTS}/{product['id']}"
    cleared = client.patch(
        url, json={"made_to_order": False, "lead_time_note": None}, headers=owner
    )
    assert (cleared.json()["made_to_order"], cleared.json()["lead_time_note"]) == (False, None)
    plain = client.post(PRODUCTS, json=product_payload(), headers=owner).json()
    assert plain["made_to_order"] is False

    for bad in ({"lead_time_note": "x" * 61}, {"lead_time_note": "   "}, {"made_to_order": None}):
        assert client.patch(url, json=bad, headers=owner).status_code == 422, bad
    staff = client.patch(url, json={"made_to_order": True}, headers=users["staff_a"].headers)
    assert staff.status_code == 403


# ---------------------------------------------------------------------------
# Personalization fields (Slice A2)
# ---------------------------------------------------------------------------
RING_FIELDS = [
    {"label": " Ring size ", "kind": "choice", "required": True, "choices": ["5", "6", "7"]},
    {"label": "Engraving", "kind": "text"},
]


def test_the_owner_sets_personalization_fields(
    client: TestClient, users: dict[str, TestUser]
) -> None:
    owner = users["owner_a"].headers
    product = client.post(PRODUCTS, json=product_payload(), headers=owner).json()
    assert product["personalization"] == []
    url = f"{PRODUCTS}/{product['id']}/personalization"

    saved = client.put(url, json={"fields": RING_FIELDS}, headers=owner)
    assert saved.status_code == 200, saved.text
    fields = saved.json()["personalization"]
    assert [
        (f["position"], f["label"], f["kind"], f["required"], f["max_length"], f["choices"])
        for f in fields
    ] == [
        (1, "Ring size", "choice", True, None, ["5", "6", "7"]),
        (2, "Engraving", "text", False, 40, None),  # text defaults to 40 characters
    ]

    # Reorder and edit while keeping ids; leave one out to remove it.
    size_id = fields[0]["id"]
    edited = client.put(
        url,
        json={
            "fields": [{"id": size_id, "label": "Size", "kind": "choice", "choices": ["6", "7"]}]
        },
        headers=owner,
    ).json()["personalization"]
    assert [(f["id"], f["label"], f["choices"]) for f in edited] == [(size_id, "Size", ["6", "7"])]
    assert (
        client.get(f"{PRODUCTS}/{product['id']}", headers=owner).json()["personalization"] == edited
    )
    assert client.put(url, json={"fields": []}, headers=owner).json()["personalization"] == []


@pytest.mark.parametrize(
    "fields",
    [
        [{"label": f"F{i}", "kind": "text"} for i in range(6)],  # six
        [{"label": "Size", "kind": "text"}, {"label": "size", "kind": "text"}],  # repeated label
        [{"label": "Size", "kind": "choice"}],  # no choices
        [{"label": "Size", "kind": "choice", "choices": []}],
        [{"label": "Size", "kind": "choice", "choices": ["A", "a"]}],
        [{"label": "Size", "kind": "choice", "choices": ["A"], "max_length": 5}],
        [{"label": "Note", "kind": "text", "choices": ["A"]}],
        [{"label": "Note", "kind": "text", "max_length": 201}],
        [{"label": "", "kind": "text"}],
        [{"label": "x" * 41, "kind": "text"}],
        [{"label": "Note", "kind": "number"}],
        [
            {"label": "Note", "kind": "text", "id": "00000000-0000-4000-8000-000000000000"}
        ],  # not ours
        [{"label": "Note", "kind": "text", "tenant_id": TENANT_B}],
    ],
)
def test_bad_personalization_fields_are_refused(client: TestClient, users, fields: list) -> None:
    owner = users["owner_a"].headers
    product = client.post(PRODUCTS, json=product_payload(), headers=owner).json()
    url = f"{PRODUCTS}/{product['id']}/personalization"
    assert client.put(url, json={"fields": fields}, headers=owner).status_code == 422
    assert client.get(f"{PRODUCTS}/{product['id']}", headers=owner).json()["personalization"] == []


def test_only_the_owner_of_this_tenant_sets_fields(client: TestClient, users) -> None:
    owner = users["owner_a"].headers
    product = client.post(PRODUCTS, json=product_payload(), headers=owner).json()
    url = f"{PRODUCTS}/{product['id']}/personalization"
    body = {"fields": RING_FIELDS}
    for name in ("staff_a", "cpa_a"):
        assert client.put(url, json=body, headers=users[name].headers).status_code == 403
    assert client.put(url, json=body, headers=users["owner_b"].headers).status_code == 404
    client.put(url, json=body, headers=owner)
    staff_view = client.get(f"{PRODUCTS}/{product['id']}", headers=users["staff_a"].headers).json()
    assert len(staff_view["personalization"]) == 2  # staff read them
