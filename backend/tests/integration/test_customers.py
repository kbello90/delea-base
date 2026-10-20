"""Customers through the real app with real tokens: role matrix, isolation, wholesale rules."""

import uuid

import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

CUSTOMERS = "/api/v1/customers"
SEED_A_SARAH = "a5000000-0000-0000-0000-000000000001"
SEED_A_BELLA = "a5000000-0000-0000-0000-000000000003"  # wholesale
SEED_B_PRIYA = "b6000000-0000-0000-0000-000000000001"


def it_first_name() -> str:
    return f"IT-{uuid.uuid4().hex[:8]}"


def retail(**overrides: object) -> dict:
    body: dict[str, object] = {
        "first_name": it_first_name(),
        "last_name": "Tester",
        "email": "it@test.local",
        "phone": "+1 555 0100",
        "address_line1": "1 Main St",
        "city": "Albany",
        "state": "NY",
        "postal_code": "12207",
        "notes": "Prefers rose gold",
    }
    body.update(overrides)
    return body


def wholesale(**overrides: object) -> dict:
    return retail(
        customer_type="wholesale",
        company_name="IT Boutique",
        tax_exempt=True,
        resale_certificate_number="NY-RS-1",
        wholesale_discount_bps=2000,
        payment_terms="net_30",
        **overrides,
    )


def create(client: TestClient, user: TestUser, body: dict) -> dict:
    response = client.post(CUSTOMERS, json=body, headers=user.headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    item = f"{CUSTOMERS}/{create(client, users['owner_a'], retail())['id']}"  # never a seed row
    assert_access(client, users, "GET", CUSTOMERS, {"owner_a": 200, "staff_a": 200, "owner_b": 200})
    assert_access(client, users, "GET", item, {"owner_a": 200, "staff_a": 200, "owner_b": 404})
    # Staff can create and update (RLS staff insert + update)...
    assert_access(
        client,
        users,
        "POST",
        CUSTOMERS,
        {"owner_a": 201, "staff_a": 201, "owner_b": 201},
        json=lambda _: retail(),
    )
    assert_access(
        client,
        users,
        "PATCH",
        item,
        {"owner_a": 200, "staff_a": 200, "owner_b": 404},
        json={"notes": "VIP"},
    )
    # ...but only the owner deletes.
    target = create(client, users["owner_a"], retail())["id"]
    assert_access(
        client, users, "DELETE", f"{CUSTOMERS}/{target}", {"owner_a": 204, "owner_b": 404}
    )


def test_separate_contact_columns_round_trip(
    client: TestClient, users: dict[str, TestUser]
) -> None:
    body = retail(address_line2="Apt 4", country="ca", marketing_opt_in=True)
    created = create(client, users["staff_a"], body)
    assert created["tenant_id"] == TENANT_A
    for field in (
        "email",
        "phone",
        "address_line1",
        "address_line2",
        "city",
        "state",
        "postal_code",
        "notes",
        "marketing_opt_in",
    ):
        assert created[field] == body[field], field
    assert created["country"] == "CA"
    assert created["customer_type"] == "retail" and created["payment_terms"] == "due_on_receipt"


def test_wholesale_customer_round_trip(client: TestClient, users: dict[str, TestUser]) -> None:
    created = create(client, users["owner_a"], wholesale())
    assert (created["customer_type"], created["company_name"], created["tax_exempt"]) == (
        "wholesale",
        "IT Boutique",
        True,
    )
    assert (created["wholesale_discount_bps"], created["payment_terms"]) == (2000, "net_30")


@pytest.mark.parametrize(
    "extra",
    [
        {"company_name": "Co"},
        {"tax_exempt": True, "resale_certificate_number": "X"},
        {"resale_certificate_number": "X"},
        {"wholesale_discount_bps": 100},
        {"payment_terms": "net_15"},
    ],
)
def test_wholesale_fields_on_retail_are_422(
    client: TestClient, users: dict[str, TestUser], db, extra: dict
) -> None:
    first = it_first_name()
    response = client.post(
        CUSTOMERS, json=retail(first_name=first, **extra), headers=users["staff_a"].headers
    )
    assert response.status_code == 422
    assert (
        db.execute(
            "select count(*) from public.customers where first_name = %s", (first,)
        ).fetchone()[0]
        == 0
    )


def test_patch_checks_the_merged_row(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    customer = create(client, owner, retail())
    url = f"{CUSTOMERS}/{customer['id']}"

    # A retail customer can't gain a wholesale field.
    response = client.patch(url, json={"wholesale_discount_bps": 500}, headers=owner.headers)
    assert response.status_code == 422
    assert "not allowed on a retail customer" in response.json()["detail"]

    # Switching to wholesale with the needed fields works...
    upgraded = client.patch(
        url,
        json={
            "customer_type": "wholesale",
            "company_name": "IT Co",
            "wholesale_discount_bps": 1000,
        },
        headers=owner.headers,
    )
    assert upgraded.status_code == 200, upgraded.text

    # ...switching back without clearing them is 422, with them cleared it works.
    assert (
        client.patch(url, json={"customer_type": "retail"}, headers=owner.headers).status_code
        == 422
    )
    downgraded = client.patch(
        url,
        json={"customer_type": "retail", "company_name": None, "wholesale_discount_bps": 0},
        headers=owner.headers,
    )
    assert downgraded.status_code == 200, downgraded.text
    assert downgraded.json()["company_name"] is None

    # Required fields can't be nulled; empty bodies are rejected.
    assert client.patch(url, json={"first_name": None}, headers=owner.headers).status_code == 422
    assert client.patch(url, json={}, headers=owner.headers).status_code == 422


def test_seeded_wholesale_customer_rules(client: TestClient, users: dict[str, TestUser]) -> None:
    url = f"{CUSTOMERS}/{SEED_A_BELLA}"
    owner = users["owner_a"]
    response = client.patch(url, json={"resale_certificate_number": None}, headers=owner.headers)
    assert response.status_code == 422  # still tax-exempt, so the certificate is required


def test_search_filter_and_isolation(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    found = client.get(CUSTOMERS, params={"q": "bella"}, headers=owner.headers).json()
    assert [c["id"] for c in found] == [SEED_A_BELLA]
    by_email = client.get(CUSTOMERS, params={"q": "michael.chang"}, headers=owner.headers).json()
    assert len(by_email) == 1
    ws = client.get(CUSTOMERS, params={"customer_type": "wholesale"}, headers=owner.headers).json()
    assert {c["customer_type"] for c in ws} == {"wholesale"}

    list_a = client.get(CUSTOMERS, params={"limit": 100}, headers=owner.headers).json()
    list_b = client.get(CUSTOMERS, params={"limit": 100}, headers=users["owner_b"].headers).json()
    assert {c["tenant_id"] for c in list_a} == {TENANT_A}
    assert {c["tenant_id"] for c in list_b} == {TENANT_B}
    assert SEED_B_PRIYA not in {c["id"] for c in list_a}
    assert client.get(CUSTOMERS, params={"q": "priya"}, headers=owner.headers).json() == []


def test_tenant_id_in_body_is_rejected(client: TestClient, users: dict[str, TestUser], db) -> None:
    first = it_first_name()
    response = client.post(
        CUSTOMERS,
        json=retail(first_name=first, tenant_id=TENANT_B),
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 422
    moved = client.patch(
        f"{CUSTOMERS}/{SEED_A_SARAH}",
        json={"tenant_id": TENANT_B},
        headers=users["owner_a"].headers,
    )
    assert moved.status_code == 422
    assert (
        db.execute(
            "select tenant_id::text from public.customers where id = %s", (SEED_A_SARAH,)
        ).fetchone()[0]
        == TENANT_A
    )


def test_staff_cannot_delete_and_row_survives(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    customer = create(client, users["owner_a"], retail())
    response = client.delete(f"{CUSTOMERS}/{customer['id']}", headers=users["staff_a"].headers)
    assert response.status_code == 403
    assert (
        db.execute(
            "select count(*) from public.customers where id = %s", (customer["id"],)
        ).fetchone()[0]
        == 1
    )
