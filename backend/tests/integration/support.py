"""Shared constants and helpers for the integration tests."""

import asyncio
import uuid
from dataclasses import dataclass

import jwt

from app.core.security import TokenClaims

TENANT_A = "11111111-1111-1111-1111-111111111111"
TENANT_B = "22222222-2222-2222-2222-222222222222"
TEST_SKU_PREFIX = "IT-"
PASSWORD = "local-integration-only-9f2c"  # local throwaway users only

START_HINT = (
    "start it with `npx supabase@2.121.0 start` and put the keys from "
    "`npx supabase@2.121.0 status -o env` in backend/.env (see backend/README.md)"
)

# psycopg's async mode cannot run on Windows' default Proactor loop.
LOOP_OPTIONS = {"loop_factory": asyncio.SelectorEventLoop}


@dataclass(frozen=True)
class TestUser:
    __test__ = False  # not a test class

    name: str
    user_id: str
    email: str
    token: str
    tenant_id: str | None
    app_role: str | None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @property
    def claims(self) -> TokenClaims:
        assert self.tenant_id and self.app_role
        return TokenClaims(sub=self.user_id, tenant_id=self.tenant_id, app_role=self.app_role)


def unverified_claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def new_sku() -> str:
    return f"{TEST_SKU_PREFIX}{uuid.uuid4().hex[:10].upper()}"


def product_payload(**overrides: object) -> dict:
    payload: dict[str, object] = {
        "sku": new_sku(),
        "name": "Integration Test Ring",
        "metal": "silver_925",
        "price_cents": 12500,
        "stock_qty": 4,
        "is_published": True,
        "sort_order": 99,
    }
    payload.update(overrides)
    return payload
