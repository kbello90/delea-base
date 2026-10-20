"""Real Supabase tokens through the real app: 401 / 403 behavior and hook-issued claims."""

import pytest
from fastapi.testclient import TestClient

from .support import TENANT_A, TENANT_B, TestUser, unverified_claims

pytestmark = pytest.mark.integration

PRODUCTS = "/api/v1/products"


@pytest.mark.parametrize(
    ("name", "tenant_id", "app_role"),
    [
        ("owner_a", TENANT_A, "owner"),
        ("staff_a", TENANT_A, "staff"),
        ("cpa_a", TENANT_A, "cpa"),
        ("owner_b", TENANT_B, "owner"),
    ],
)
def test_issued_tokens_carry_membership_claims(
    users: dict[str, TestUser], name: str, tenant_id: str, app_role: str
) -> None:
    token = users[name].token
    claims = unverified_claims(token)
    assert claims["tenant_id"] == tenant_id
    assert claims["app_role"] == app_role
    assert claims["role"] == "authenticated"  # the Postgres role claim is untouched
    # Auth may issue aud as a string or a list; PyJWT's audience check accepts both.
    aud = claims["aud"]
    assert "authenticated" in (aud if isinstance(aud, list) else [aud])


def test_tokens_are_es256(users: dict[str, TestUser]) -> None:
    import jwt

    assert jwt.get_unverified_header(users["owner_a"].token)["alg"] == "ES256"


def test_no_membership_token_has_no_tenant_claims(users: dict[str, TestUser]) -> None:
    claims = unverified_claims(users["no_membership"].token)
    assert "tenant_id" not in claims and "app_role" not in claims


def test_no_token_is_401(client: TestClient) -> None:
    response = client.get(PRODUCTS)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_garbage_token_is_401(client: TestClient) -> None:
    response = client.get(PRODUCTS, headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_anon_key_is_not_a_user_token(client: TestClient, settings) -> None:
    response = client.get(
        PRODUCTS, headers={"Authorization": f"Bearer {settings.supabase_anon_key}"}
    )
    assert response.status_code == 401


def test_user_without_membership_is_403(client: TestClient, users: dict[str, TestUser]) -> None:
    response = client.get(PRODUCTS, headers=users["no_membership"].headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "No tenant membership"}


def test_health_ready_reaches_the_database(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
