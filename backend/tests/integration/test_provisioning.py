"""Tenant provisioning against the local stack: rows, members' claims, isolation from the
seed tenants (API and RLS), and demo-only deletion."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.auth_admin import AuthAdmin
from app.services.provisioning import (
    Member,
    ProvisioningError,
    TenantSpec,
    delete_demo_tenant,
    provision_tenant,
    remove_tenant_storage,
)

from .support import PASSWORD, TENANT_A, TENANT_B, run_async, sign_in, unverified_claims

pytestmark = pytest.mark.integration

SEED_PRODUCT_A = "a1000000-0000-0000-0000-000000000001"


async def _connect(settings):
    return await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True)


async def _provision(settings, spec: TenantSpec) -> uuid.UUID:
    async with await _connect(settings) as conn:
        return await provision_tenant(conn, spec)


async def _delete(settings, tenant_id) -> bool:
    async with await _connect(settings) as conn:
        return await delete_demo_tenant(conn, tenant_id)


@pytest.fixture(scope="module")
def tenant(settings) -> Iterator[dict]:
    """A provisioned demo tenant with an owner, staff and cpa, signed in."""
    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    run = uuid.uuid4().hex[:8]
    emails = {role: f"it-prov-{role}-{run}@test.local" for role in ("owner", "staff", "cpa")}
    ids = {role: run_async(admin.ensure_user(email, PASSWORD)) for role, email in emails.items()}
    tenant_id = uuid.uuid4()
    try:
        created = run_async(
            _provision(
                settings,
                TenantSpec(
                    tenant_id=tenant_id,
                    name="IT Provisioned Shop",
                    business_name="IT Provisioned Jewelry",
                    is_demo=True,
                    default_tax_rate_bps=800,
                    members=tuple(Member(ids[r], r) for r in ("owner", "staff", "cpa")),
                ),
            )
        )
        assert created == tenant_id
        tokens = {
            role: sign_in(settings.supabase_url, settings.supabase_anon_key, email, PASSWORD)
            for role, email in emails.items()
        }
        yield {"id": str(tenant_id), "user_ids": ids, "tokens": tokens}
    finally:
        run_async(_delete(settings, tenant_id))
        for user_id in ids.values():
            run_async(admin.delete_user(user_id))


def _headers(tenant: dict, role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tenant['tokens'][role]}"}


def test_the_tenant_and_its_settings_brand_and_members_exist(tenant, db) -> None:
    tid = tenant["id"]
    assert db.execute(
        "select name, tier::text, is_demo, is_active from public.tenants where id = %s", (tid,)
    ).fetchone() == ("IT Provisioned Shop", "essential", True, True)
    assert db.execute(
        "select currency, default_tax_rate_bps, next_quote_number, next_invoice_number "
        "from public.company_settings where tenant_id = %s",
        (tid,),
    ).fetchone() == ("USD", 800, 1, 1)
    assert db.execute(
        "select business_name, primary_color, accent_color from public.brand_config "
        "where tenant_id = %s",
        (tid,),
    ).fetchone() == ("IT Provisioned Jewelry", "#6B2737", "#B8935B")
    members = dict(
        db.execute(
            "select user_id::text, app_role::text from public.tenant_members where tenant_id = %s",
            (tid,),
        ).fetchall()
    )
    assert members == {user_id: role for role, user_id in tenant["user_ids"].items()}


def test_members_sign_in_with_the_new_tenant_in_their_claims(tenant) -> None:
    for role in ("owner", "staff", "cpa"):
        claims = unverified_claims(tenant["tokens"][role])
        assert (claims["tenant_id"], claims["app_role"]) == (tenant["id"], role)


def test_a_member_of_the_new_tenant_sees_nothing_of_tenant_a_or_b_through_the_api(
    tenant, client: TestClient
) -> None:
    owner = _headers(tenant, "owner")
    assert client.get("/api/v1/products", headers=owner).json() == []
    assert client.get("/api/v1/materials", headers=owner).json() == []
    assert client.get("/api/v1/customers", headers=owner).json() == []
    assert client.get("/api/v1/invoices", headers=owner).json() == []
    assert client.get("/api/v1/expenses", headers=owner).json() == []
    assert client.get(f"/api/v1/products/{SEED_PRODUCT_A}", headers=owner).status_code == 404
    summary = client.get("/api/v1/dashboard/summary", headers=owner).json()
    assert summary["ytd"]["revenue_cents"] == 0 and summary["low_stock_products"] == 0
    assert client.get("/api/v1/products", headers=_headers(tenant, "staff")).json() == []
    assert client.get("/api/v1/invoices", headers=_headers(tenant, "cpa")).json() == []


@pytest.mark.parametrize("role", ["owner", "staff", "cpa"])
def test_a_member_of_the_new_tenant_sees_no_tenant_a_or_b_rows_through_rls(
    tenant, db, role: str
) -> None:
    claims = {"sub": tenant["user_ids"][role], "role": "authenticated"}
    claims |= {"tenant_id": tenant["id"], "app_role": role}
    tables = ("tenants", "products", "materials", "customers", "invoices", "expenses")
    with db.transaction():
        db.execute("select set_config('request.jwt.claims', %s, true)", (_json(claims),))
        db.execute("set local role authenticated")
        for table in tables:
            column = "id" if table == "tenants" else "tenant_id"
            (seen,) = db.execute(
                f"select count(*) from public.{table} where {column} = any(%s::uuid[])",
                ([TENANT_A, TENANT_B],),
            ).fetchone()
            assert seen == 0, f"{role} of the new tenant sees {seen} {table} rows of A or B"
        db.execute("reset role")


def _json(value: dict) -> str:
    import json

    return json.dumps(value)


def test_an_invalid_spec_is_refused_before_anything_is_written(settings, db) -> None:
    owner = Member(str(uuid.uuid4()), "owner")
    bad = [
        TenantSpec(name="X", business_name="X", members=()),
        TenantSpec(
            name="X", business_name="X", members=(owner, Member(str(uuid.uuid4()), "owner"))
        ),
        TenantSpec(name="X", business_name="X", members=(owner,), default_tax_rate_bps=10_001),
        TenantSpec(name=" ", business_name="X", members=(owner,)),
    ]
    before = db.execute("select count(*) from public.tenants").fetchone()[0]
    for spec in bad:
        with pytest.raises(ProvisioningError):
            run_async(_provision(settings, spec))
    assert db.execute("select count(*) from public.tenants").fetchone()[0] == before


def test_a_failure_part_way_leaves_nothing_behind(settings, db) -> None:
    # The member's user doesn't exist in auth.users: the last insert fails, all roll back.
    tenant_id = uuid.uuid4()
    spec = TenantSpec(
        tenant_id=tenant_id,
        name="IT Half Shop",
        business_name="IT Half",
        members=(Member(str(uuid.uuid4()), "owner"),),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        run_async(_provision(settings, spec))
    for table, column in (
        ("tenants", "id"),
        ("company_settings", "tenant_id"),
        ("brand_config", "tenant_id"),
    ):
        count = db.execute(f"select count(*) from public.{table} where {column} = %s", (tenant_id,))
        assert count.fetchone()[0] == 0, table


def test_only_a_demo_tenant_can_be_deleted(settings, db) -> None:
    with pytest.raises(ProvisioningError, match="not a demo tenant"):
        run_async(_delete(settings, uuid.UUID(TENANT_A)))
    assert (
        db.execute("select count(*) from public.tenants where id = %s", (TENANT_A,)).fetchone()[0]
        == 1
    )
    assert run_async(_delete(settings, uuid.uuid4())) is False  # nothing to delete


async def _remove_storage(settings, tenant_id) -> int:
    async with await _connect(settings) as conn:
        return await remove_tenant_storage(conn, tenant_id)


def test_storage_of_a_tenant_that_is_not_a_demo_is_never_removed(settings, db) -> None:
    before = db.execute(
        "select count(*) from storage.objects where name like %s", (f"{TENANT_A}/%",)
    ).fetchone()
    with pytest.raises(ProvisioningError, match="not a demo tenant"):
        run_async(_remove_storage(settings, uuid.UUID(TENANT_A)))
    after = db.execute(
        "select count(*) from storage.objects where name like %s", (f"{TENANT_A}/%",)
    ).fetchone()
    assert after == before


def test_deleting_a_demo_tenant_removes_all_its_rows(settings, db) -> None:
    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    user_id = run_async(
        admin.ensure_user(f"it-prov-del-{uuid.uuid4().hex[:8]}@test.local", PASSWORD)
    )
    tenant_id = uuid.uuid4()
    try:
        run_async(
            _provision(
                settings,
                TenantSpec(
                    tenant_id=tenant_id,
                    name="IT Doomed Shop",
                    business_name="IT Doomed",
                    is_demo=True,
                    members=(Member(user_id, "owner"),),
                    domains=(("it-doomed.test:5173", "storefront"),),
                ),
            )
        )
        db.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents) "
            "values (%s, 'IT-DOOMED-1', 'IT Doomed', 'other', 100)",
            (tenant_id,),
        )
        assert run_async(_delete(settings, tenant_id)) is True
        for table, column in (
            ("tenants", "id"),
            ("company_settings", "tenant_id"),
            ("brand_config", "tenant_id"),
            ("tenant_members", "tenant_id"),
            ("tenant_domains", "tenant_id"),
            ("products", "tenant_id"),
        ):
            count = db.execute(
                f"select count(*) from public.{table} where {column} = %s", (tenant_id,)
            )
            assert count.fetchone()[0] == 0, table
    finally:
        run_async(admin.delete_user(user_id))


def test_the_currency_is_fixed_once_the_tenant_has_data(client: TestClient, tenant, db) -> None:
    """Provisioning sets the currency; the API never changes it; the database allows a change
    only while the tenant has no products, materials, invoices or expenses."""
    owner = _headers(tenant, "owner")
    assert client.get("/api/v1/settings/currency", headers=owner).json() == {"currency": "USD"}
    refused = client.patch("/api/v1/settings/company", json={"currency": "CAD"}, headers=owner)
    assert refused.status_code == 422

    # Still empty: an administrative fix is possible, and every role reads the new currency.
    db.execute(
        "update public.company_settings set currency = 'CAD' where tenant_id = %s", (tenant["id"],)
    )
    for role in ("owner", "staff", "cpa"):
        got = client.get("/api/v1/settings/currency", headers=_headers(tenant, role)).json()
        assert got == {"currency": "CAD"}, role

    # Once there is data, nobody can change it.
    product = client.post(
        "/api/v1/products",
        json={"sku": "IT-CUR-1", "name": "IT currency ring", "metal": "other", "price_cents": 100},
        headers=owner,
    )
    assert product.status_code == 201, product.text
    try:
        with pytest.raises(psycopg.errors.CheckViolation, match="can't change once you have"):
            db.execute(
                "update public.company_settings set currency = 'EUR' where tenant_id = %s",
                (tenant["id"],),
            )
        assert client.get("/api/v1/settings/currency", headers=owner).json() == {"currency": "CAD"}
    finally:
        db.execute("delete from public.products where id = %s", (product.json()["id"],))
        db.execute(
            "update public.company_settings set currency = 'USD' where tenant_id = %s",
            (tenant["id"],),
        )


def test_an_unsupported_currency_is_refused_at_provisioning(settings, db) -> None:
    spec = TenantSpec(
        name="IT bad currency",
        business_name="IT",
        members=(Member(str(uuid.uuid4()), "owner"),),
        currency="JPY",
    )
    with pytest.raises(ProvisioningError, match="currency must be one of"):
        run_async(_provision(settings, spec))
