"""DB session/connection helpers (TRD locked decision 6).

The backend talks to Postgres directly through a psycopg async pool. Three kinds of session,
each one transaction:

- user_session: every authenticated request. Runs as `authenticated` with the caller's
  verified JWT claims, so RLS is the enforcement.
- service_session: no role switch. Public endpoints, Stripe webhooks, automation worker only.
- ai_session: the AI assistant. Runs as `ai_readonly`, scoped by `app.tenant_id` (TRD §3.2).

Schema is owned by supabase/migrations/, never by the backend.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings
from app.core.security import TokenClaims


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


async def apply_user_scope(conn: AsyncConnection, claims: TokenClaims) -> None:
    """Switch the current transaction to `authenticated` with the caller's verified claims."""
    await conn.execute("set local role authenticated")
    await conn.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (
            json.dumps(
                {
                    "sub": claims.sub,
                    "role": "authenticated",
                    "tenant_id": claims.tenant_id,
                    "app_role": claims.app_role,
                }
            ),
        ),
    )


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
    """One transaction as `authenticated` for the verified caller.

    Commits when the block exits normally and rolls back on any exception.
    """
    async with pool.connection() as conn, conn.transaction():
        await apply_user_scope(conn, claims)
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

    The connection role must be granted `ai_readonly` (supabase/README.md).
    """
    async with pool.connection() as conn, conn.transaction():
        await apply_ai_scope(conn, claims)
        yield conn
