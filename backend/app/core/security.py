"""JWT validation (ES256) and app_role checks.

Supabase Auth signs access tokens with ES256. A custom access token hook adds the
`tenant_id` and `app_role` claims (TRD §2.2). The top-level `role` claim is the Postgres
role (`authenticated` / `anon`) and is not used for app authorization.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, cast, get_args

import jwt
from fastapi import HTTPException, status

from app.core.config import get_settings

AppRole = Literal["owner", "staff", "cpa"]
APP_ROLES: frozenset[str] = frozenset(get_args(AppRole))


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    tenant_id: str
    app_role: AppRole


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    # Created on first use; fetches the Supabase project's public signing keys.
    return jwt.PyJWKClient(get_settings().supabase_jwks_url)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_access_token(token: str) -> TokenClaims:
    """Verify an ES256 Supabase access token and extract tenant_id / app_role."""
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=get_settings().jwt_audience,
        )
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid token") from exc

    sub = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    app_role = payload.get("app_role")
    if not sub or not tenant_id:
        raise _unauthorized("Token is missing sub or tenant_id")
    if app_role not in APP_ROLES:
        raise _unauthorized("Token has a missing or unknown app_role")

    return TokenClaims(sub=sub, tenant_id=tenant_id, app_role=cast(AppRole, app_role))


def ensure_role(claims: TokenClaims, allowed: set[AppRole]) -> None:
    if claims.app_role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
