"""Expenses through the real app with real tokens: role matrix, isolation, filters, CSV."""

import csv
import io
import uuid

import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, assert_access, force_currency

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
        "category": "other",
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
    created = create(client, owner, amount_cents=5000)
    assert created["tenant_id"] == TENANT_A and created["amount_cents"] == 5000
    assert (created["has_receipt"], created["receipt_content_type"]) == (False, None)
    assert "receipt_path" not in created and "receipt_url" not in created
    url = f"{EXPENSES}/{created['id']}"
    updated = client.patch(
        url, json={"is_tax_deductible": False, "category": "packaging"}, headers=owner.headers
    )
    assert updated.status_code == 200, updated.text
    assert (updated.json()["is_tax_deductible"], updated.json()["category"]) == (False, "packaging")
    assert client.patch(url, json={"expense_date": None}, headers=owner.headers).status_code == 422
    assert client.patch(url, json={"category": None}, headers=owner.headers).status_code == 422
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
    assert amounts(q="SILVER") == [45000]  # description, any case
    assert amounts(q="box") == [14500]
    assert amounts(q="100%") == []  # % is literal
    for bad_category in ("Materials", "services", "it-test"):
        response = client.get(EXPENSES, params={"category": bad_category}, headers=owner.headers)
        assert response.status_code == 422, bad_category  # only the fixed list
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
        "currency",
        "amount",
        "is_tax_deductible",
        "has_receipt",
    ]
    assert {r["has_receipt"] for r in rows} == {"false"}


def test_csv_export_currency_is_the_tenants(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    a = parse_csv(client.get(EXPORT, headers=users["cpa_a"].headers).text)
    assert a and {r["currency"] for r in a} == {"USD"}
    force_currency(db, TENANT_B, "CAD")
    try:
        b = parse_csv(client.get(EXPORT, headers=users["owner_b"].headers).text)
        assert b and {r["currency"] for r in b} == {"CAD"}
    finally:
        force_currency(db, TENANT_B, "USD")


def test_csv_export_escapes_formula_cells(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
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
    # The category is a fixed list now, so the free text that reaches the CSV is the
    # description.
    # The descriptions can't start with IT- here, so the rows are removed explicitly.
    try:
        for key, (text, _) in dangerous.items():
            create(client, owner, description=f"{text} {key}", expense_date="2026-09-01")
        rows = parse_csv(
            client.get(EXPORT, params={"date_to": "2026-09-01"}, headers=owner.headers).text
        )
        by_key = {r["description"].rsplit(" ", 1)[-1]: r["description"] for r in rows}
        for key, (_, expected) in dangerous.items():
            assert by_key[key] == f"{expected} {key}", key
    finally:
        db.execute("delete from public.expenses where description like %s", (f"% IT-{tag}-%",))


# ---------------------------------------------------------------------------
# Summary: the same filters as the list, over every page
# ---------------------------------------------------------------------------
def test_summary_totals_equal_the_sum_over_all_pages(client: TestClient, users) -> None:
    owner = users["owner_a"]
    tag = uuid.uuid4().hex[:6]
    rows = [
        ("2026-08-01", "materials", 1001, True),
        ("2026-08-02", "materials", 2002, False),
        ("2026-08-03", "shipping", 3003, True),
        ("2026-08-04", "meals", 4004, False),
        ("2026-08-05", "materials", 5005, True),
        ("2026-08-06", "software", 6006, True),
        ("2026-08-07", "materials", 7007, True),
    ]
    for day, category, cents, deductible in rows:
        create(
            client,
            owner,
            description=f"IT-{tag} {category}",
            expense_date=day,
            category=category,
            amount_cents=cents,
            is_tax_deductible=deductible,
        )

    for filters in (
        {"q": tag},
        {"q": tag, "category": "materials"},
        {"q": tag, "tax_deductible": "true"},
        {"q": tag, "date_from": "2026-08-03", "date_to": "2026-08-06"},
        {"q": f"{tag} materials", "tax_deductible": "false"},
    ):
        listed, offset = [], 0
        while True:  # page through 3 at a time, like the admin's pages
            page = client.get(
                EXPENSES, params={**filters, "limit": 3, "offset": offset}, headers=owner.headers
            ).json()
            listed += page
            if len(page) < 3:
                break
            offset += 3
        summary = client.get(f"{EXPENSES}/summary", params=filters, headers=owner.headers)
        assert summary.status_code == 200, summary.text
        assert summary.json() == {
            "count": len(listed),
            "total_cents": sum(e["amount_cents"] for e in listed),
            "deductible_cents": sum(e["amount_cents"] for e in listed if e["is_tax_deductible"]),
        }, filters
    everything = client.get(f"{EXPENSES}/summary", params={"q": tag}, headers=owner.headers)
    assert everything.json() == {"count": 7, "total_cents": 28028, "deductible_cents": 22022}
    bad = client.get(
        f"{EXPENSES}/summary",
        params={"date_from": "2026-08-10", "date_to": "2026-08-01"},
        headers=owner.headers,
    )
    assert bad.status_code == 422  # the same parser as the list


# ---------------------------------------------------------------------------
# Regression: the category enum changed nothing in the dashboard or the AI summary
# ---------------------------------------------------------------------------
def test_dashboard_and_ai_summary_agree_with_the_raw_expenses(client, users, db) -> None:
    owner = users["owner_a"]
    dash = client.get("/api/v1/dashboard/summary", headers=owner.headers).json()
    year_start, today = db.execute(
        "select date_trunc('year', now() at time zone 'UTC')::date, "
        "(now() at time zone 'UTC')::date"
    ).fetchone()
    expected = db.execute(
        "select coalesce(sum(amount_cents), 0), "
        "coalesce(sum(amount_cents) filter (where is_tax_deductible), 0) "
        "from public.expenses where tenant_id = %s and expense_date between %s and %s",
        (TENANT_A, year_start, today),
    ).fetchone()
    assert (dash["ytd"]["expenses_cents"], dash["ytd"]["deductible_expenses_cents"]) == expected

    monthly = client.get("/api/v1/dashboard/monthly", headers=owner.headers).json()
    for month in monthly["months"]:
        raw = db.execute(
            "select coalesce(sum(amount_cents), 0) from public.expenses where tenant_id = %s "
            "and date_trunc('month', expense_date)::date = %s and expense_date <= %s",
            (TENANT_A, month["month_start"], today),
        ).fetchone()[0]
        assert month["expenses_cents"] == raw, month["month_start"]

    with db.transaction(force_rollback=True):
        db.execute("select set_config('app.tenant_id', %s, true)", (TENANT_A,))
        ai = db.execute(
            "select month, category::text, expense_count, total_cents, deductible_cents "
            "from ai.expense_summary order by 1, 2"
        ).fetchall()
    raw = db.execute(
        "select date_trunc('month', expense_date)::date, category::text, count(*)::int, "
        "sum(amount_cents)::bigint, "
        "coalesce(sum(amount_cents) filter (where is_tax_deductible), 0)::bigint "
        "from public.expenses where tenant_id = %s group by 1, 2 order by 1, 2",
        (TENANT_A,),
    ).fetchall()
    assert ai == raw
    # The seed's three categories this October, as before the enum.
    october = {row[1]: row[3] for row in ai if str(row[0]) == "2026-10-01"}
    assert october == {"marketing": 25000, "materials": 59500, "meals": 6800}
