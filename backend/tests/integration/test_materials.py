"""Materials through the real app with real tokens: role matrix, isolation, filters, errors."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

MATERIALS = "/api/v1/materials"
SEED_A_PLATING = "a3000000-0000-0000-0000-000000000002"  # 3 vials, threshold 5 (low)
SEED_B_BAILS = "b4000000-0000-0000-0000-000000000001"


def it_name() -> str:
    return f"IT-material-{uuid.uuid4().hex[:8]}"


def payload(**overrides: object) -> dict:
    body: dict[str, object] = {
        "name": it_name(),
        "unit": "pcs",
        "unit_cost_cents": 250,
        "qty_on_hand": "10.50",
        "reorder_threshold": "4",
    }
    body.update(overrides)
    return body


def create(client: TestClient, user: TestUser, **overrides: object) -> dict:
    response = client.post(MATERIALS, json=payload(**overrides), headers=user.headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    item = f"{MATERIALS}/{SEED_A_PLATING}"
    # Reads: owner and staff of A; B's owner gets an empty-looking tenant (404 on A's row).
    assert_access(client, users, "GET", MATERIALS, {"owner_a": 200, "staff_a": 200, "owner_b": 200})
    assert_access(client, users, "GET", item, {"owner_a": 200, "staff_a": 200, "owner_b": 404})
    # Writes: owner only. B's owner creates in B, never in A.
    assert_access(
        client, users, "POST", MATERIALS, {"owner_a": 201, "owner_b": 201}, json=lambda _: payload()
    )
    assert_access(
        client, users, "PATCH", item, {"owner_a": 200, "owner_b": 404}, json={"unit": "vials"}
    )
    target = create(client, users["owner_a"])["id"]
    assert_access(
        client, users, "DELETE", f"{MATERIALS}/{target}", {"owner_a": 204, "owner_b": 404}
    )


def test_owner_crud_with_exact_quantities(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    created = create(client, owner, qty_on_hand=12.25, reorder_threshold="3.5")
    assert created["tenant_id"] == TENANT_A
    assert (created["qty_on_hand"], created["reorder_threshold"]) == ("12.25", "3.50")
    assert created["is_low"] is False

    url = f"{MATERIALS}/{created['id']}"
    updated = client.patch(
        url, json={"qty_on_hand": "1.75", "unit_cost_cents": 300}, headers=owner.headers
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["qty_on_hand"] == "1.75"
    assert updated.json()["is_low"] is True
    assert updated.json()["unit_cost_cents"] == 300

    assert client.delete(url, headers=owner.headers).status_code == 204
    assert client.get(url, headers=owner.headers).status_code == 404


def test_low_stock_filter_and_search(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    low = client.get(MATERIALS, params={"low_stock": "true"}, headers=owner.headers).json()
    assert [m["id"] for m in low] == [SEED_A_PLATING]  # 3 < 5
    not_low = client.get(MATERIALS, params={"low_stock": "false"}, headers=owner.headers).json()
    assert SEED_A_PLATING not in {m["id"] for m in not_low} and len(not_low) == 2

    found = client.get(MATERIALS, params={"q": "silver"}, headers=owner.headers).json()
    assert [m["name"] for m in found] == ["925 Silver blanks"]
    assert client.get(MATERIALS, params={"q": "_"}, headers=owner.headers).json() == []

    page = client.get(MATERIALS, params={"limit": 2, "offset": 1}, headers=owner.headers).json()
    everything = client.get(MATERIALS, headers=owner.headers).json()
    assert [m["id"] for m in page] == [m["id"] for m in everything][1:3]


def test_lists_never_cross_tenants(client: TestClient, users: dict[str, TestUser]) -> None:
    list_a = client.get(MATERIALS, headers=users["owner_a"].headers).json()
    list_b = client.get(MATERIALS, headers=users["owner_b"].headers).json()
    assert {m["tenant_id"] for m in list_a} == {TENANT_A}
    assert {m["tenant_id"] for m in list_b} == {TENANT_B}
    assert SEED_B_BAILS not in {m["id"] for m in list_a}


def test_staff_cannot_write(client: TestClient, users: dict[str, TestUser], db) -> None:
    staff = users["staff_a"]
    name = it_name()
    assert client.post(MATERIALS, json=payload(name=name), headers=staff.headers).status_code == 403
    assert (
        db.execute("select count(*) from public.materials where name = %s", (name,)).fetchone()[0]
        == 0
    )


def test_tenant_id_in_body_is_rejected(client: TestClient, users: dict[str, TestUser], db) -> None:
    name = it_name()
    response = client.post(
        MATERIALS, json=payload(name=name, tenant_id=TENANT_B), headers=users["owner_a"].headers
    )
    assert response.status_code == 422
    assert (
        db.execute("select count(*) from public.materials where name = %s", (name,)).fetchone()[0]
        == 0
    )


def test_duplicate_name_is_409_and_bad_values_422(
    client: TestClient, users: dict[str, TestUser]
) -> None:
    owner = users["owner_a"]
    name = it_name()
    create(client, owner, name=name)
    assert client.post(MATERIALS, json=payload(name=name), headers=owner.headers).status_code == 409
    # Same name is fine in another tenant.
    assert (
        client.post(
            MATERIALS, json=payload(name=name), headers=users["owner_b"].headers
        ).status_code
        == 201
    )
    for bad in ({"qty_on_hand": "1.234"}, {"qty_on_hand": -1}, {"unit_cost_cents": -5}):
        assert client.post(MATERIALS, json=payload(**bad), headers=owner.headers).status_code == 422


def test_quantity_update_emits_material_low_once(
    client: TestClient, users: dict[str, TestUser], db: psycopg.Connection
) -> None:
    owner = users["owner_a"]
    material = create(client, owner, qty_on_hand="8", reorder_threshold="5")
    url = f"{MATERIALS}/{material['id']}"
    client.patch(url, json={"qty_on_hand": "4"}, headers=owner.headers)
    client.patch(url, json={"qty_on_hand": "3"}, headers=owner.headers)
    events = db.execute(
        "select count(*) from public.automation_events "
        "where event_type = 'material_low' and payload ->> 'material_id' = %s",
        (material["id"],),
    ).fetchone()[0]
    assert events == 1  # the database trigger, crossing only; nothing duplicated in Python
