"""Fixtures for integration tests against the local Supabase stack.

Everything here is skipped, with a message saying how to start the stack, when the database
or Auth is unreachable or the local keys are missing from backend/.env. A plain `pytest`
therefore passes without Docker (and in CI).

Users are real Supabase Auth users created through the Auth admin API with the LOCAL
service-role key, signed in through /auth/v1/token, and deleted afterwards.
"""

import os
import uuid
from collections.abc import Iterator

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings

from .support import (
    LOOP_OPTIONS,
    PASSWORD,
    START_HINT,
    TENANT_A,
    TENANT_B,
    TEST_SKU_PREFIX,
    TestUser,
    ThrowawayTenant,
    run_async,
    sign_in,
)


def _unavailable(message: str) -> None:
    """Skip when the stack is unavailable, or fail if DELEA_REQUIRE_INTEGRATION=1 (CI)."""
    if os.environ.get("DELEA_REQUIRE_INTEGRATION") == "1":
        pytest.fail(message, pytrace=False)
    pytest.skip(message)


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = get_settings()
    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", s.supabase_url),
            ("SUPABASE_ANON_KEY", s.supabase_anon_key),
            ("SUPABASE_SERVICE_ROLE_KEY", s.supabase_service_role_key),
            ("DATABASE_URL", s.database_url),
        )
        if not value
    ]
    if missing:
        _unavailable(f"Integration tests need {', '.join(missing)} in backend/.env; {START_HINT}")

    try:
        with psycopg.connect(s.database_url, connect_timeout=3) as conn:
            conn.execute("select 1")
    except psycopg.OperationalError:
        _unavailable(f"Local Supabase database not reachable at {s.database_url}; {START_HINT}")

    try:
        httpx.get(
            f"{s.supabase_url}/auth/v1/health", headers={"apikey": s.supabase_anon_key}, timeout=3
        ).raise_for_status()
    except httpx.HTTPError:
        _unavailable(f"Local Supabase Auth not reachable at {s.supabase_url}; {START_HINT}")
    return s


@pytest.fixture(scope="session")
def db(settings: Settings) -> Iterator[psycopg.Connection]:
    """Direct connection as the local connection role (bypasses RLS). Setup/checks only."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        yield conn


def _admin_headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


@pytest.fixture(scope="session")
def users(settings: Settings, db: psycopg.Connection) -> Iterator[dict[str, TestUser]]:
    specs = {
        "owner_a": (TENANT_A, "owner"),
        "staff_a": (TENANT_A, "staff"),
        "cpa_a": (TENANT_A, "cpa"),
        "owner_b": (TENANT_B, "owner"),
        "no_membership": (None, None),
    }
    run = uuid.uuid4().hex[:8]
    created: list[str] = []
    result: dict[str, TestUser] = {}
    with httpx.Client(base_url=f"{settings.supabase_url}/auth/v1", timeout=10) as auth:
        try:
            for name, (tenant_id, app_role) in specs.items():
                email = f"it-{name.replace('_', '-')}-{run}@test.local"
                response = auth.post(
                    "/admin/users",
                    headers=_admin_headers(settings),
                    json={"email": email, "password": PASSWORD, "email_confirm": True},
                )
                response.raise_for_status()
                user_id = response.json()["id"]
                created.append(user_id)

                # The membership must exist before sign-in: the access token hook reads it.
                if tenant_id:
                    db.execute(
                        "insert into public.tenant_members (user_id, tenant_id, app_role) "
                        "values (%s, %s, %s)",
                        (user_id, tenant_id, app_role),
                    )

                token_response = auth.post(
                    "/token",
                    params={"grant_type": "password"},
                    headers={"apikey": settings.supabase_anon_key},
                    json={"email": email, "password": PASSWORD},
                )
                token_response.raise_for_status()
                token = token_response.json()["access_token"]
                result[name] = TestUser(name, user_id, email, token, tenant_id, app_role)
            yield result
        finally:
            for user_id in created:
                auth.delete(f"/admin/users/{user_id}", headers=_admin_headers(settings))


@pytest.fixture(scope="session")
def client(settings: Settings) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app, backend_options=LOOP_OPTIONS) as test_client:
        yield test_client


# Rows created by the tests carry the IT- prefix in one identifying column per table.
_TEST_ROWS = (
    ("products", "sku"),
    ("materials", "name"),
    ("customers", "first_name"),
    ("expenses", "description"),
    ("invoices", "customer_name"),
)


@pytest.fixture(autouse=True)
def _cleanup_test_rows(db: psycopg.Connection) -> Iterator[None]:
    """Remove every row the tests created (IT- prefix), whatever the test did, and put the
    document counters back (creating an invoice advances them)."""
    counters = db.execute(
        "select tenant_id, next_quote_number, next_invoice_number from public.company_settings"
    ).fetchall()
    yield
    for table, column in _TEST_ROWS:
        db.execute(f"delete from public.{table} where {column} like %s", (f"{TEST_SKU_PREFIX}%",))
    for tenant_id, next_quote, next_invoice in counters:
        db.execute(
            "update public.company_settings set next_quote_number = %s, next_invoice_number = %s "
            "where tenant_id = %s",
            (next_quote, next_invoice, tenant_id),
        )


# Seed tables whose rows the tests must never change (updated_at is ignored: a no-op PATCH
# still bumps it). company_settings counters are covered too.
_SEED_TABLES = (
    "tenants",
    "tenant_domains",
    "brand_config",
    "company_settings",
    "storefront_settings",
    "products",
    "materials",
    "product_materials",
    "customers",
    "invoices",
    "invoice_line_items",
    "expenses",
    "automation_rules",
    "reorder_list",
)


def _seed_fingerprint(db: psycopg.Connection) -> dict[str, str]:
    """A hash of tenant A's and B's rows per seed table. Other tenants (the showcase, tenants
    the tests provision) may come and go; A and B must not change."""
    fingerprint = {}
    for table in _SEED_TABLES:
        column = "id" if table == "tenants" else "tenant_id"
        fingerprint[table] = db.execute(
            f"select coalesce(md5(string_agg((to_jsonb(t) - 'updated_at')::text, '|' "
            f"order by (to_jsonb(t) - 'updated_at')::text)), '') from public.{table} t "
            f"where t.{column} = any(%s::uuid[])",
            ([TENANT_A, TENANT_B],),
        ).fetchone()[0]
    return fingerprint


@pytest.fixture(scope="session", autouse=True)
def _seed_rows_unchanged(db: psycopg.Connection) -> Iterator[None]:
    """Fail the run if any test left a seed row modified (tests must use their own IT- rows)."""
    before = _seed_fingerprint(db)
    yield
    after = _seed_fingerprint(db)
    changed = sorted(t for t in _SEED_TABLES if before[t] != after[t])
    assert not changed, f"integration tests changed seed data in: {', '.join(changed)}"


@pytest.fixture(scope="session", autouse=True)
def _cleanup_automation_events(db: psycopg.Connection) -> Iterator[None]:
    """Triggers write automation_events (e.g. stock_low); drop the ones this run produced."""
    started_at = db.execute("select clock_timestamp()").fetchone()[0]
    yield
    db.execute("delete from public.automation_events where created_at >= %s", (started_at,))


@pytest.fixture
def anyio_backend() -> tuple[str, dict]:
    return ("asyncio", LOOP_OPTIONS)


@pytest.fixture
def make_tenant(settings: Settings, db: psycopg.Connection) -> Iterator:
    """A factory for throwaway tenants (never the seed tenants A and B), each with one signed-in
    member per requested role. Provisioned as a demo tenant, as provisioning requires for
    anything deletable; `is_demo=False` then clears the flag (administrative connection).
    Afterwards each tenant is deleted, with the Auth users of everyone who was a member of it,
    invited users included."""
    from app.core.auth_admin import AuthAdmin
    from app.services.provisioning import Member, TenantSpec, provision_tenant

    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    tenants: list[str] = []
    users: set[str] = set()

    async def provision(spec: TenantSpec) -> uuid.UUID:
        async with await psycopg.AsyncConnection.connect(
            settings.database_url, autocommit=True
        ) as conn:
            return await provision_tenant(conn, spec)

    def make(*, is_demo: bool = False, roles: tuple[str, ...] = ("owner",)) -> ThrowawayTenant:
        run = uuid.uuid4().hex[:8]
        emails = {role: f"it-team-{role}-{run}@test.local" for role in roles}
        ids = {}
        for role, email in emails.items():
            ids[role] = run_async(admin.ensure_user(email, PASSWORD))
            users.add(ids[role])
        tenant_id = str(uuid.uuid4())
        run_async(
            provision(
                TenantSpec(
                    tenant_id=uuid.UUID(tenant_id),
                    name=f"IT Team {run}",
                    business_name=f"IT Team Jewelry {run}",
                    is_demo=True,
                    members=tuple(Member(ids[role], role) for role in roles),
                )
            )
        )
        tenants.append(tenant_id)
        if not is_demo:
            db.execute("update public.tenants set is_demo = false where id = %s", (tenant_id,))
        tokens = {
            role: sign_in(settings.supabase_url, settings.supabase_anon_key, email, PASSWORD)
            for role, email in emails.items()
        }
        return ThrowawayTenant(tenant_id, f"IT Team Jewelry {run}", emails, ids, tokens)

    yield make

    for tenant_id in tenants:
        members = db.execute(
            "select user_id::text from public.tenant_members where tenant_id = %s", (tenant_id,)
        ).fetchall()
        users.update(user_id for (user_id,) in members)
        db.execute("update public.tenants set is_demo = true where id = %s", (tenant_id,))
        db.execute("delete from public.tenants where id = %s and is_demo", (tenant_id,))
    for user_id in users:
        run_async(admin.delete_user(user_id))
