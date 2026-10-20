"""Unit tests for decode_access_token. No network, no database: the JWKS client is replaced
with one that serves a locally generated EC P-256 key."""

import time
import uuid
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException

from app.core import security

TENANT_A = "11111111-1111-1111-1111-111111111111"
SIGNING_KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


class FakeJWKSClient:
    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=SIGNING_KEY.public_key())


class UnreachableJWKSClient:
    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        raise jwt.PyJWKClientConnectionError("connection refused")


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_jwks_client", lambda: FakeJWKSClient())


def make_token(key=SIGNING_KEY, algorithm: str = "ES256", **overrides: object) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "aud": "authenticated",
        "role": "authenticated",
        "iat": now,
        "exp": now + 3600,
        "tenant_id": TENANT_A,
        "app_role": "owner",
    }
    for name, value in overrides.items():
        if value is None:
            claims.pop(name, None)
        else:
            claims[name] = value
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "test-key"})


def assert_status(token: str, expected: int) -> HTTPException:
    with pytest.raises(HTTPException) as info:
        security.decode_access_token(token)
    assert info.value.status_code == expected
    return info.value


def test_valid_token_returns_claims() -> None:
    sub = str(uuid.uuid4())
    claims = security.decode_access_token(make_token(sub=sub, app_role="staff"))
    assert claims == security.TokenClaims(sub=sub, tenant_id=TENANT_A, app_role="staff")


def test_missing_tenant_id_is_forbidden() -> None:
    error = assert_status(make_token(tenant_id=None), 403)
    assert error.detail == "No tenant membership"


def test_non_uuid_tenant_id_is_forbidden() -> None:
    assert_status(make_token(tenant_id="not-a-uuid"), 403)


@pytest.mark.parametrize("app_role", ["admin", "", None])
def test_invalid_or_missing_app_role_is_forbidden(app_role: str | None) -> None:
    assert_status(make_token(app_role=app_role), 403)


def test_postgres_role_claim_is_not_an_app_role() -> None:
    # Only app_role grants access; the reserved `role` claim is never used for it.
    assert_status(make_token(app_role=None, role="owner"), 403)


def test_wrong_algorithm_is_rejected() -> None:
    hs_token = make_token(key="a-shared-secret-that-is-long-enough-32b", algorithm="HS256")
    assert_status(hs_token, 401)


def test_signature_from_another_key_is_rejected() -> None:
    assert_status(make_token(key=OTHER_KEY), 401)


def test_expired_token_is_rejected() -> None:
    assert_status(make_token(exp=int(time.time()) - 60), 401)


def test_token_without_exp_is_rejected() -> None:
    assert_status(make_token(exp=None), 401)


def test_wrong_audience_is_rejected() -> None:
    assert_status(make_token(aud="anon"), 401)


def test_missing_sub_is_rejected() -> None:
    assert_status(make_token(sub=None), 401)


def test_garbage_token_is_rejected() -> None:
    error = assert_status("not.a.jwt", 401)
    assert error.headers == {"WWW-Authenticate": "Bearer"}


def test_unreachable_jwks_is_service_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security, "_jwks_client", lambda: UnreachableJWKSClient())
    assert_status(make_token(), 503)


def test_ensure_role() -> None:
    claims = security.TokenClaims(sub="s", tenant_id=TENANT_A, app_role="cpa")
    security.ensure_role(claims, {"cpa"})
    with pytest.raises(HTTPException) as info:
        security.ensure_role(claims, {"owner", "staff"})
    assert info.value.status_code == 403
