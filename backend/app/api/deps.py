"""Shared FastAPI dependencies (auth, tenant context)."""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.security import AppRole, TokenClaims, decode_access_token, ensure_role

bearer_scheme = HTTPBearer()


def get_current_claims(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> TokenClaims:
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
