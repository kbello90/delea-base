"""Expenses through the real app with real tokens: role matrix, isolation, filters, CSV."""

import csv
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access

pytestmark = pytest.mark.integration

EXPENSES = "/api/v1/expenses"
EXPORT = f"{EXPENSES}/export.csv"

# Seed (tenant A): 2026-10-02 materials 45000, 2026-10-05 materials 14500,
# 2026-10-10 marketing 25000, 2026-10-12 meals 6800 (not deductible). Tenant B: 1 row.


def it_description() -> str:
    return f"IT-{uuid.uuid4().hex[:8]}"


def payload(**overrides: object) -> dict:
    body: dict[str, object] = {
        "expense_date": "2026-10-15",
        "description": it_description(),
        "category": "it-test",
        "amount_cents": 1234,
        "is_tax_deductible": True,
    }
    body.update(overrides)
    return body


def create(client: TestClient, user: TestUser, **overrides: object) -> dict:
    response = client.post(EXPENSES, json=payload(**overrides), headers=user.headers)
    assert response.status_code == 201, response.text
    return response.json()


def parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def test_role_matrix(client: TestClient, users: dict[str, TestUser]) -> None:
    item = f"{EXPENSES}/{create(client, users['owner_a'])['id']}"
    # Owner and CPA read; staff has no access at all (RLS).
    read = {"owner_a": 200, "cpa_a": 200, "owner_b": 200}
    assert_access(client, users, "GET", EXPENSES, read)
    assert_access(client, users, "GET", EXPORT, read)
    assert_access(client, users, "GET", item, {"owner_a": 200, "cpa_a": 200, "owner_b": 404})
    # Writes: owner only.
    assert_access(
        client, users, "POST", EXPENSES, {"owner_a": 201, "owner_b": 201}, json=lambda _: payload()
    )
    assert_access(
        client, users, "PATCH", item, {"owner_a": 200, "owner_b": 404}, json={"amount_cents": 999}
    )
    target = f"{EXPENSES}/{create(client, users['owner_a'])['id']}"
    assert_access(client, users, "DELETE", target, {"owner_a": 204, "owner_b": 404})


def test_owner_crud(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    created = create(client, owner, amount_cents=5000, receipt_url="https://example.com/r.pdf")
    assert created["tenant_id"] == TENANT_A and created["amount_cents"] == 5000
    url = f"{EXPENSES}/{created['id']}"
    updated = client.patch(
        url, json={"is_tax_deductible": False, "category": None}, headers=owner.headers
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["is_tax_deductible"], updated.json()["category"]) == (False, None)
    assert client.patch(url, json={"expense_date": None}, headers=owner.headers).status_code == 422
    assert client.delete(url, headers=owner.headers).status_code == 204
    assert client.get(url, headers=owner.headers).status_code == 404


def test_cpa_cannot_write(client: TestClient, users: dict[str, TestUser], db) -> None:
    cpa = users["cpa_a"]
    description = it_description()
    assert (
        client.post(
            EXPENSES, json=payload(description=description), headers=cpa.headers
        ).status_code
        == 403
    )
    assert (
        db.execute(
            "select count(*) from public.expenses where description = %s", (description,)
        ).fetchone()[0]
        == 0
    )


def test_filters(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]

    def amounts(**params: object) -> list[int]:
        response = client.get(EXPENSES, params=params, headers=owner.headers)
        assert response.status_code == 200, response.text
        return sorted(e["amount_cents"] for e in response.json())

    assert amounts() == [6800, 14500, 25000, 45000]
    assert amounts(date_from="2026-10-05", date_to="2026-10-10") == [14500, 25000]  # inclusive
    assert amounts(category="materials") == [14500, 45000]
    assert amounts(category="Materials") == []  # exact match
    assert amounts(tax_deductible="false") == [6800]
    assert amounts(tax_deductible="true", date_to="2026-10-04") == [45000]
    assert amounts(limit=2, offset=1) == [14500, 25000]  # newest first: 10-12, 10-10, 10-05
    bad = client.get(
        EXPENSES, params={"date_from": "2026-10-10", "date_to": "2026-10-01"}, headers=owner.headers
    )
    assert bad.status_code == 422


def test_isolation(client: TestClient, users: dict[str, TestUser]) -> None:
    list_a = client.get(EXPENSES, params={"limit": 100}, headers=users["cpa_a"].headers).json()
    list_b = client.get(EXPENSES, params={"limit": 100}, headers=users["owner_b"].headers).json()
    assert {e["tenant_id"] for e in list_a} == {TENANT_A}
    assert {e["tenant_id"] for e in list_b} == {TENANT_B}
    csv_b = parse_csv(client.get(EXPORT, headers=users["owner_b"].headers).text)
    assert [r["description"] for r in csv_b] == ["Gem certification"]


def test_tenant_id_in_body_is_rejected(client: TestClient, users: dict[str, TestUser], db) -> None:
    description = it_description()
    response = client.post(
        EXPENSES,
        json=payload(description=description, tenant_id=TENANT_B),
        headers=users["owner_a"].headers,
    )
    assert response.status_code == 422
    assert (
        db.execute(
            "select count(*) from public.expenses where description = %s", (description,)
        ).fetchone()[0]
        == 0
    )


def test_csv_export_matches_filters_and_formats_money(
    client: TestClient, users: dict[str, TestUser]
) -> None:
    response = client.get(EXPORT, params={"category": "materials"}, headers=users["cpa_a"].headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="expenses.csv"' in response.headers["content-disposition"]
    rows = parse_csv(response.text)
    assert [(r["expense_date"], r["amount"], r["is_tax_deductible"]) for r in rows] == [
        ("2026-10-02", "450.00", "true"),
        ("2026-10-05", "145.00", "true"),
    ]
    assert list(rows[0].keys()) == [
        "expense_date",
        "description",
        "category",
        "amount",
        "is_tax_deductible",
        "receipt_url",
    ]


def test_csv_export_escapes_formula_cells(client: TestClient, users: dict[str, TestUser]) -> None:
    owner = users["owner_a"]
    tag = uuid.uuid4().hex[:6]
    dangerous = {
        f"IT-{tag}-eq": ('=HYPERLINK("http://evil")', '\'=HYPERLINK("http://evil")'),
        f"IT-{tag}-plus": ("+1+1", "'+1+1"),
        f"IT-{tag}-minus": ("-2", "'-2"),
        f"IT-{tag}-at": ("@SUM(A1)", "'@SUM(A1)"),
        f"IT-{tag}-tab": ("\tcmd", "'\tcmd"),
        f"IT-{tag}-cr": ("\rcmd", "'\rcmd"),
    }
    for description, (category, _) in dangerous.items():
        create(client, owner, description=description, category=category, expense_date="2026-09-01")

    rows = parse_csv(
        client.get(EXPORT, params={"date_to": "2026-09-01"}, headers=owner.headers).text
    )
    by_description = {r["description"]: r["category"] for r in rows}
    for description, (_, expected) in dangerous.items():
        assert by_description[description] == expected, description
