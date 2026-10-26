"""JWT validation (ES256) and app_role checks.

Supabase Auth signs access tokens with ES256. A custom access token hook adds the
`tenant_id` and `app_role` claims (TRD §2.2). The top-level `role` claim is the Postgres
role (`authenticated` / `anon`) and is not used for app authorization.

Status codes: 401 when the token itself is missing, malformed, expired, or fails
verification, or when its membership claims are no longer current (user_session re-checks
them on every request); 403 when a valid token carries no usable membership (no tenant_id or
no valid app_role); 503 when the signing keys cannot be fetched.

Browsers tell these apart by status and header, never by message text:
- 401: refresh the session once and retry (packages/api-client). A refreshed token carries the
  membership as it is now (the access token hook reads tenant_members), so a role change
  takes effect on the retry.
- 403 with `X-Delea-Access: no-membership`: the account has no business membership; the admin
  shows its no-access page.
"""

import uuid
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


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


# Response header that marks a 403 as "no business membership" (exposed through CORS).
ACCESS_HEADER = "X-Delea-Access"
NO_MEMBERSHIP = "no-membership"


def _no_membership(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail=detail, headers={ACCESS_HEADER: NO_MEMBERSHIP}
    )


def membership_not_current() -> HTTPException:
    """The token is valid but its (user, tenant, role) no longer matches a membership of an
    active tenant: removed, role changed, or tenant deactivated since it was issued."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Membership no longer valid",
        headers={
            "WWW-Authenticate": 'Bearer error="invalid_token", '
            'error_description="membership changed"'
        },
    )


def _is_uuid(value: object) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def decode_access_token(token: str) -> TokenClaims:
    """Verify an ES256 Supabase access token and extract tenant_id / app_role."""
    try:
        signing_key = _jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience=get_settings().jwt_audience,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWKClientConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is temporarily unavailable",
        ) from exc
    except jwt.PyJWTError as exc:
        raise _unauthorized("Invalid token") from exc

    sub = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    app_role = payload.get("app_role")
    if not sub:
        raise _unauthorized("Token is missing sub")
    # A verified token without a membership: the user is known but has no tenant access.
    if not tenant_id or not _is_uuid(tenant_id):
        raise _no_membership("No tenant membership")
    if app_role not in APP_ROLES:
        raise _no_membership("No valid app_role")

    return TokenClaims(sub=sub, tenant_id=str(tenant_id), app_role=cast(AppRole, app_role))


def ensure_role(claims: TokenClaims, allowed: set[AppRole]) -> None:
    if claims.app_role not in allowed:
        raise _forbidden("Insufficient role")
