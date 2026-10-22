"""DB session layer against the local database: transactions, scopes, RLS, the AI read path,
and the error mapping of real database errors."""

import uuid

import psycopg
import pytest
from fastapi import Depends, HTTPException
from fastapi.testclient import TestClient
from psycopg import errors
from psycopg_pool import AsyncConnectionPool

from app.api.deps import DbConn, require_role
from app.core.config import Settings
from app.core.errors import map_db_error
from app.core.security import TokenClaims
from app.db.session import apply_ai_scope, apply_user_scope, create_pool, open_pool, user_session

from .support import TENANT_A, TestUser, new_sku

pytestmark = pytest.mark.integration

SARAH_PAID_INVOICE = "a7000000-0000-0000-0000-000000000001"


def owner_claims() -> TokenClaims:
    return TokenClaims(sub=str(uuid.uuid4()), tenant_id=TENANT_A, app_role="owner")


async def make_pool(settings: Settings) -> AsyncConnectionPool:
    pool = create_pool(settings)
    await open_pool(pool)
    await pool.wait(timeout=10)
    return pool


def sku_exists(db: psycopg.Connection, sku: str) -> bool:
    return db.execute(
        "select exists (select 1 from public.products where sku = %s)", (sku,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# user_session
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_user_session_runs_as_authenticated_with_claims(settings: Settings) -> None:
    claims = owner_claims()
    pool = await make_pool(settings)
    try:
        async with user_session(pool, claims) as conn:
            cur = await conn.execute("select current_user, auth.jwt()")
            current_user, jwt_claims = await cur.fetchone()
        assert current_user == "authenticated"
        assert jwt_claims == {
            "sub": claims.sub,
            "role": "authenticated",
            "tenant_id": TENANT_A,
            "app_role": "owner",
        }
        # The role and claims do not leak into the next use of the pooled connection.
        async with pool.connection() as conn:
            cur = await conn.execute(
                "select current_user, current_setting('request.jwt.claims', true)"
            )
            role_after, claims_after = await cur.fetchone()
        assert role_after != "authenticated"
        assert not claims_after
    finally:
        await pool.close()


@pytest.mark.anyio
async def test_user_session_rolls_back_on_exception(settings: Settings, db) -> None:
    sku = new_sku()
    pool = await make_pool(settings)
    try:
        with pytest.raises(RuntimeError):
            async with user_session(pool, owner_claims()) as conn:
                await conn.execute(
                    "insert into public.products (tenant_id, sku, name, metal, price_cents) "
                    "values (%s, %s, 'Rollback', 'other', 100)",
                    (TENANT_A, sku),
                )
                raise RuntimeError("boom")
    finally:
        await pool.close()
    assert not sku_exists(db, sku)


@pytest.mark.anyio
async def test_rls_blocks_staff_writes_even_without_require_role(settings: Settings, db) -> None:
    sku = new_sku()
    staff = TokenClaims(sub=str(uuid.uuid4()), tenant_id=TENANT_A, app_role="staff")
    pool = await make_pool(settings)
    try:
        with pytest.raises(errors.InsufficientPrivilege) as info:
            async with user_session(pool, staff) as conn:
                await conn.execute(
                    "insert into public.products (tenant_id, sku, name, metal, price_cents) "
                    "values (%s, %s, 'Staff Write', 'other', 100)",
                    (TENANT_A, sku),
                )
        mapped = map_db_error(info.value)
        assert (mapped.status_code, mapped.detail) == (403, "Not allowed")
    finally:
        await pool.close()
    assert not sku_exists(db, sku)


# ---------------------------------------------------------------------------
# get_db rollback through a real request (test-only route, removed afterwards)
# ---------------------------------------------------------------------------
@pytest.fixture
def failing_routes(client: TestClient):
    app = client.app
    before = list(app.router.routes)

    async def write_then_http_error(conn: DbConn, sku: str) -> None:
        await conn.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents) "
            "values (%s, %s, 'Partial', 'other', 100)",
            (TENANT_A, sku),
        )
        raise HTTPException(status_code=418, detail="failed after writing")

    async def write_then_db_error(conn: DbConn, sku: str) -> None:
        await conn.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents) "
            "values (%s, %s, 'Partial', 'other', 100)",
            (TENANT_A, sku),
        )
        # Second statement fails: DL-NK-01 already exists in tenant A.
        await conn.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents) "
            "values (%s, 'DL-NK-01', 'Duplicate', 'other', 100)",
            (TENANT_A,),
        )

    async def fails_only_at_commit(conn: DbConn, sku: str) -> dict[str, str]:
        await conn.execute(
            "insert into public.products (tenant_id, sku, name, metal, price_cents) "
            "values (%s, %s, 'Partial', 'other', 100)",
            (TENANT_A, sku),
        )
        # A deferred unique constraint is only checked at COMMIT, after this handler returns.
        await conn.execute(
            "create temp table _commit_check (x int unique deferrable initially deferred) "
            "on commit drop"
        )
        await conn.execute("insert into _commit_check values (1), (1)")
        return {"status": "handler finished"}

    owner_only = [Depends(require_role("owner"))]
    app.add_api_route(
        "/_test/fails-only-at-commit",
        fails_only_at_commit,
        methods=["POST"],
        dependencies=owner_only,
    )
    app.add_api_route(
        "/_test/write-then-http-error",
        write_then_http_error,
        methods=["POST"],
        dependencies=owner_only,
    )
    app.add_api_route(
        "/_test/write-then-db-error", write_then_db_error, methods=["POST"], dependencies=owner_only
    )
    yield
    app.router.routes[:] = before


@pytest.mark.usefixtures("failing_routes")
def test_commit_failure_is_reported_never_a_false_200(
    client: TestClient, users: dict[str, TestUser], db
) -> None:
    # get_db commits before the response is sent (Depends scope="function"), so an error
    # raised by COMMIT itself reaches the client instead of a 200 for unsaved data.
    sku = new_sku()
    response = client.post(
        "/_test/fails-only-at-commit", params={"sku": sku}, headers=users["owner_a"].headers
    )
    assert response.status_code == 409
    assert not sku_exists(db, sku)


@pytest.mark.usefixtures("failing_routes")
@pytest.mark.parametrize(
    ("path", "status_code"),
    [("/_test/write-then-http-error", 418), ("/_test/write-then-db-error", 409)],
)
def test_failed_request_leaves_no_partial_writes(
    client: TestClient, users: dict[str, TestUser], db, path: str, status_code: int
) -> None:
    sku = new_sku()
    response = client.post(path, params={"sku": sku}, headers=users["owner_a"].headers)
    assert response.status_code == status_code
    assert not sku_exists(db, sku)


# ---------------------------------------------------------------------------
# AI read path. The grant is made inside the test's own transaction and rolled back;
# it is never in a migration.
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_ai_scope_reads_views_for_its_tenant_only(settings: Settings) -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as conn:
        async with conn.transaction(force_rollback=True):
            await conn.execute("grant ai_readonly to current_user")
            await apply_ai_scope(conn, owner_claims())

            cur = await conn.execute("select current_user, current_setting('app.tenant_id')")
            assert await cur.fetchone() == ("ai_readonly", TENANT_A)

            cur = await conn.execute("select sku from ai.inventory_status order by sku")
            skus = [row[0] for row in await cur.fetchall()]
            assert skus == sorted(
                ["DL-NK-01", "DL-FL-02", "DL-RG-01", "DL-CH-01", "DL-RG-02", "DL-NK-02"]
            )

            # Every public table is out of reach.
            for table in ("products", "invoices", "company_settings", "tenant_members"):
                with pytest.raises(errors.InsufficientPrivilege):
                    async with conn.transaction():  # savepoint, so the outer one survives
                        await conn.execute(f"select 1 from public.{table} limit 1")
            with pytest.raises(errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(
                        "update public.products set name = name where tenant_id = %s", (TENANT_A,)
                    )


@pytest.mark.anyio
async def test_ai_role_without_tenant_returns_no_rows(settings: Settings) -> None:
    async with await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as conn:
        async with conn.transaction(force_rollback=True):
            await conn.execute("grant ai_readonly to current_user")
            await conn.execute("set local role ai_readonly")  # no app.tenant_id set
            for view in (
                "inventory_status",
                "materials_status",
                "sales_summary",
                "expense_summary",
                "tax_summary",
            ):
                cur = await conn.execute(f"select count(*) from ai.{view}")
                assert (await cur.fetchone())[0] == 0, view


@pytest.mark.anyio
async def test_ai_scope_needs_the_grant(settings: Settings) -> None:
    # Without `grant ai_readonly to <connection role>` the switch itself is refused.
    async with await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as conn:
        with pytest.raises(errors.InsufficientPrivilege):
            async with conn.transaction(force_rollback=True):
                await apply_ai_scope(conn, owner_claims())


# ---------------------------------------------------------------------------
# Error mapping of real database errors
# ---------------------------------------------------------------------------
def test_trigger_message_is_surfaced_for_backward_status(db) -> None:
    with pytest.raises(errors.CheckViolation) as info, db.transaction(force_rollback=True):
        db.execute(
            "update public.invoices set order_status = 'quote' where id = %s", (SARAH_PAID_INVOICE,)
        )
    mapped = map_db_error(info.value)
    assert mapped.status_code == 422
    assert mapped.detail == "This invoice can't move back to quote."


@pytest.mark.anyio
async def test_staff_guard_message_is_surfaced(settings: Settings) -> None:
    staff = TokenClaims(sub=str(uuid.uuid4()), tenant_id=TENANT_A, app_role="staff")
    async with await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True
    ) as conn:
        with pytest.raises(errors.InsufficientPrivilege) as info:
            async with conn.transaction(force_rollback=True):
                await apply_user_scope(conn, staff)
                await conn.execute(
                    "update public.invoices set total_cents = 1 where id = %s",
                    (SARAH_PAID_INVOICE,),
                )
    mapped = map_db_error(info.value)
    assert mapped.status_code == 403
    assert mapped.detail == "staff cannot change amounts on an invoice past quote"


def test_constraint_messages_stay_generic(db) -> None:
    with pytest.raises(errors.CheckViolation) as info, db.transaction(force_rollback=True):
        db.execute(
            "insert into public.customers (tenant_id, customer_type, first_name) "
            "values (%s, 'wholesale', 'X')",
            (TENANT_A,),
        )
    mapped = map_db_error(info.value)
    assert mapped.status_code == 422
    assert mapped.detail == "The request violates a data rule"
    assert "customers" not in mapped.detail
