"""The direct Supabase data API is closed: with real owner, staff and cpa tokens (and the anon
key), /rest/v1 and /graphql/v1 can neither read nor write public tables. Auth keeps working.
All application access goes through FastAPI (TRD §1.3).
"""

import httpx
import psycopg
import pytest

from app.core.config import Settings

from .support import TENANT_A, TestUser

pytestmark = pytest.mark.integration

ROLES = ("owner_a", "staff_a", "cpa_a")
TABLES = ("products", "invoices", "customers", "company_settings", "expenses", "tenant_members")


@pytest.fixture(scope="module")
def api(settings: Settings):
    with httpx.Client(base_url=settings.supabase_url, timeout=10) as client:
        yield client


def headers(settings: Settings, user: TestUser | None) -> dict[str, str]:
    token = user.token if user else settings.supabase_anon_key
    return {"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"}


def snapshot(db: psycopg.Connection) -> tuple:
    return db.execute(
        "select (select count(*) from public.customers), (select count(*) from public.products),"
        " (select string_agg(name, ',' order by id) from public.products)"
    ).fetchone()


@pytest.mark.parametrize("who", [*ROLES, "anon"])
def test_rest_cannot_read_public_tables(api, settings, users, who: str) -> None:
    h = headers(settings, users.get(who))
    for table in TABLES:
        for extra in ({}, {"Accept-Profile": "public"}):
            response = api.get(f"/rest/v1/{table}", params={"select": "*"}, headers={**h, **extra})
            assert response.status_code >= 400, (who, table, extra, response.text)
            assert "tenant_id" not in response.text


@pytest.mark.parametrize("who", [*ROLES, "anon"])
def test_rest_cannot_write_public_tables(api, settings, users, db, who: str) -> None:
    h = headers(settings, users.get(who))
    before = snapshot(db)
    writes = [
        api.post(
            "/rest/v1/customers", headers=h, json={"tenant_id": TENANT_A, "first_name": "IT-rest"}
        ),
        api.post(
            "/rest/v1/customers",
            headers={**h, "Content-Profile": "public"},
            json={"tenant_id": TENANT_A, "first_name": "IT-rest"},
        ),
        api.patch(
            "/rest/v1/products", params={"sku": "eq.DL-NK-01"}, headers=h, json={"name": "IT-rest"}
        ),
        api.delete("/rest/v1/expenses", params={"tenant_id": f"eq.{TENANT_A}"}, headers=h),
        api.post("/rest/v1/rpc/current_tax_rate_bps", headers=h, json={}),
    ]
    for response in writes:
        assert response.status_code >= 400, (who, response.request.method, response.text)
    assert snapshot(db) == before


@pytest.mark.parametrize("who", [*ROLES, "anon"])
def test_graphql_sees_no_public_tables(api, settings, users, db, who: str) -> None:
    h = headers(settings, users.get(who))
    before = snapshot(db)
    queries = [
        "{ productsCollection(first: 5) { edges { node { sku tenant_id } } } }",
        "{ invoicesCollection(first: 5) { edges { node { total_cents } } } }",
        'mutation { insertIntocustomersCollection(objects: [{first_name: "IT-gql"}]) '
        "{ affectedCount } }",
    ]
    for query in queries:
        response = api.post("/graphql/v1", headers=h, json={"query": query})
        body = (
            response.json()
            if response.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        assert not (body.get("data") or {}), (who, query, response.text)
        assert "DL-NK-01" not in response.text
    intro = api.post(
        "/graphql/v1", headers=h, json={"query": "{ __schema { queryType { fields { name } } } }"}
    )
    assert "Collection" not in intro.text
    assert snapshot(db) == before


@pytest.mark.parametrize("who", ROLES)
def test_auth_still_works(api, settings, users, who: str) -> None:
    user = users[who]
    response = api.get("/auth/v1/user", headers=headers(settings, user))
    assert response.status_code == 200
    assert response.json()["id"] == user.user_id
