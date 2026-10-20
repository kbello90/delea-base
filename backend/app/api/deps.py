"""Shared FastAPI dependencies (auth, tenant context, database session)."""

from collections.abc import AsyncIterator, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.security import AppRole, TokenClaims, decode_access_token, ensure_role
from app.db.session import user_session

# auto_error=False so a missing header is a 401 with our message, not the library default.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_claims(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> TokenClaims:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_access_token(credentials.credentials)


CurrentClaims = Annotated[TokenClaims, Depends(get_current_claims)]


def get_tenant_id(claims: CurrentClaims) -> str:
    return claims.tenant_id


TenantId = Annotated[str, Depends(get_tenant_id)]


def require_role(*roles: AppRole) -> Callable[[TokenClaims], TokenClaims]:
    """Dependency factory: `Depends(require_role("owner", "staff"))`."""
    allowed = set(roles)

    def dependency(claims: CurrentClaims) -> TokenClaims:
        ensure_role(claims, allowed)
        return claims

    return dependency


def get_pool(request: Request) -> AsyncConnectionPool:
    pool: AsyncConnectionPool | None = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database is not configured"
        )
    return pool


async def get_db(
    claims: CurrentClaims,
    pool: Annotated[AsyncConnectionPool, Depends(get_pool)],
) -> AsyncIterator[AsyncConnection]:
    """The verified caller's user session: one transaction, RLS enforced.

    Used through `DbConn`, whose scope="function" makes the commit (or rollback) happen
    when the endpoint returns, before the response is sent.
    """
    async with user_session(pool, claims) as conn:
        yield conn


DbConn = Annotated[AsyncConnection, Depends(get_db, scope="function")]
