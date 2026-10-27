"""DB session/connection helpers (TRD locked decision 6).

The backend talks to Postgres directly through a psycopg async pool. Five kinds of session,
each one transaction:

- user_session: every authenticated request. Re-checks the membership, then runs as
  `authenticated` with the caller's verified JWT claims, so RLS is the enforcement.
- storefront_session: public storefront reads. A read-only transaction as `storefront_reader`,
  which can select from the storefront views only, scoped by `app.tenant_id` (the tenant
  resolved from the request's Origin host, never from request input).
- checkout_session: the public checkout. As `storefront_writer`, which can only execute the
  storefront checkout functions (SECURITY DEFINER, tenant re-checked inside).
- service_session: no role switch. Stripe webhooks, the automation worker, password reset.
- ai_session: the AI assistant. Runs as `ai_readonly`, scoped by `app.tenant_id` (TRD §3.2).

Schema is owned by supabase/migrations/, never by the backend.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.security import TokenClaims, membership_not_current


def create_pool(settings: Settings) -> AsyncConnectionPool:
    """Build the pool without connecting. Opened in the FastAPI lifespan."""
    return AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        # Autocommit connections: every unit of work is an explicit transaction() block,
        # so nothing leaks between requests sharing a pooled connection.
        kwargs={"autocommit": True},
        open=False,
        name="delea",
    )


async def open_pool(pool: AsyncConnectionPool) -> None:
    # wait=False: the app starts even if the database is down; /health/ready reports it.
    await pool.open(wait=False)


async def close_pool(pool: AsyncConnectionPool) -> None:
    await pool.close()


def _claims_json(claims: TokenClaims) -> str:
    return json.dumps(
        {
            "sub": claims.sub,
            "role": "authenticated",
            "tenant_id": claims.tenant_id,
            "app_role": claims.app_role,
        }
    )


async def apply_user_scope(conn: AsyncConnection, claims: TokenClaims) -> None:
    """Switch the current transaction to `authenticated` with the caller's verified claims.

    No membership re-check (user_session adds it); tests use this to exercise RLS directly.
    """
    await conn.execute("set local role authenticated")
    await conn.execute("select set_config('request.jwt.claims', %s, true)", (_claims_json(claims),))


async def ensure_current_member(conn: AsyncConnection, claims: TokenClaims) -> None:
    """Refuse (401) a token whose membership changed after it was issued.

    Claims are fixed when a token is issued (about an hour). This checks, on the connection's
    own role and before any role switch, that tenant_members still has (user, tenant, role)
    and the tenant is active: two primary-key lookups (public.is_current_member).
    """
    cur = await conn.execute(
        "select public.is_current_member(%s, %s, %s)",
        (claims.sub, claims.tenant_id, claims.app_role),
    )
    row = await cur.fetchone()
    if not (row and row[0]):
        raise membership_not_current()


async def apply_checked_user_scope(conn: AsyncConnection, claims: TokenClaims) -> None:
    """The membership re-check and the claims in one statement, then the role switch."""
    cur = await conn.execute(
        "select public.is_current_member(%s, %s, %s), set_config('request.jwt.claims', %s, true)",
        (claims.sub, claims.tenant_id, claims.app_role, _claims_json(claims)),
    )
    row = await cur.fetchone()
    if not (row and row[0]):
        raise membership_not_current()
    await conn.execute("set local role authenticated")


async def apply_ai_scope(conn: AsyncConnection, claims: TokenClaims) -> None:
    """Switch the current transaction to `ai_readonly`, scoped to the caller's tenant.

    The tenant always comes from verified claims, never from request input.
    """
    await conn.execute("set local role ai_readonly")
    await conn.execute("select set_config('app.tenant_id', %s, true)", (claims.tenant_id,))


@asynccontextmanager
async def user_session(
    pool: AsyncConnectionPool, claims: TokenClaims
) -> AsyncIterator[AsyncConnection]:
    """One transaction as `authenticated` for the verified caller, whose membership is
    re-checked first (401 if it changed since the token was issued).

    Commits when the block exits normally and rolls back on any exception.
    """
    async with pool.connection() as conn, conn.transaction():
        await apply_checked_user_scope(conn, claims)
        yield conn


@asynccontextmanager
async def storefront_session(
    pool: AsyncConnectionPool, tenant_id: str | None = None
) -> AsyncIterator[AsyncConnection]:
    """One read-only transaction as `storefront_reader` (TRD §1.3, §6.11).

    The role selects from the storefront views only, which list public columns and filter on
    `app.tenant_id`; with no tenant set they return nothing (except `storefront.hosts`, which
    maps a host to its tenant). The connection role must be granted `storefront_reader`.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("set transaction read only")
        await conn.execute("set local role storefront_reader")
        if tenant_id is not None:
            await conn.execute("select set_config('app.tenant_id', %s, true)", (tenant_id,))
        yield conn


@asynccontextmanager
async def checkout_session(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    """One transaction as `storefront_writer` (TRD §6.11).

    The role has no table grants; it may execute the storefront checkout functions only
    (storefront_price_cart, storefront_find_order, storefront_count_attempt,
    storefront_place_order). The connection role must be granted `storefront_writer`.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("set local role storefront_writer")
        yield conn


@asynccontextmanager
async def payments_session(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    """One transaction as `payments_writer` (TRD §6.14, decision K4).

    No table grants: it may execute the Stripe functions only (stripe_apply_session,
    stripe_apply_refund, stripe_apply_account, stripe_record_event, stripe_link_account,
    stripe_close_session). Used by the webhook and Connect onboarding. The connection role
    must be granted `payments_writer`.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("set local role payments_writer")
        yield conn


@asynccontextmanager
async def service_session(pool: AsyncConnectionPool) -> AsyncIterator[AsyncConnection]:
    """One transaction with NO role switch: the connection's own role, RLS not applied.

    Only for public storefront endpoints, Stripe webhooks and the automation worker. Never use
    it for a request made by a signed-in user. In production the pool must connect as a
    dedicated non-superuser role, never as `postgres`.
    """
    async with pool.connection() as conn, conn.transaction():
        yield conn


@asynccontextmanager
async def ai_session(
    pool: AsyncConnectionPool, claims: TokenClaims
) -> AsyncIterator[AsyncConnection]:
    """One transaction as `ai_readonly`, which can only select from the `ai` views.

    The connection role must be granted `ai_readonly` (supabase/README.md). The membership is
    re-checked first, as in user_session.
    """
    async with pool.connection() as conn, conn.transaction():
        await ensure_current_member(conn, claims)
        await apply_ai_scope(conn, claims)
        yield conn
