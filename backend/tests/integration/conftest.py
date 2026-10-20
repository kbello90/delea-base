"""Fixtures for integration tests against the local Supabase stack.

Everything here is skipped, with a message saying how to start the stack, when the database
or Auth is unreachable or the local keys are missing from backend/.env. A plain `pytest`
therefore passes without Docker (and in CI).

Users are real Supabase Auth users created through the Auth admin API with the LOCAL
service-role key, signed in through /auth/v1/token, and deleted afterwards.
"""

import uuid
from collections.abc import Iterator

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings

from .support import (
    LOOP_OPTIONS,
    PASSWORD,
    START_HINT,
    TENANT_A,
    TENANT_B,
    TEST_SKU_PREFIX,
    TestUser,
)


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = get_settings()
    missing = [
        name
        for name, value in (
            ("SUPABASE_URL", s.supabase_url),
            ("SUPABASE_ANON_KEY", s.supabase_anon_key),
            ("SUPABASE_SERVICE_ROLE_KEY", s.supabase_service_role_key),
            ("DATABASE_URL", s.database_url),
        )
        if not value
    ]
    if missing:
        pytest.skip(f"Integration tests need {', '.join(missing)} in backend/.env; {START_HINT}")

    try:
        with psycopg.connect(s.database_url, connect_timeout=3) as conn:
            conn.execute("select 1")
    except psycopg.OperationalError:
        pytest.skip(f"Local Supabase database not reachable at {s.database_url}; {START_HINT}")

    try:
        httpx.get(
            f"{s.supabase_url}/auth/v1/health", headers={"apikey": s.supabase_anon_key}, timeout=3
        ).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Local Supabase Auth not reachable at {s.supabase_url}; {START_HINT}")
    return s


@pytest.fixture(scope="session")
def db(settings: Settings) -> Iterator[psycopg.Connection]:
    """Direct connection as the local connection role (bypasses RLS). Setup/checks only."""
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        yield conn


def _admin_headers(settings: Settings) -> dict[str, str]:
    key = settings.supabase_service_role_key
    return {"apikey": key, "Authorization": f"Bearer {key}"}


@pytest.fixture(scope="session")
def users(settings: Settings, db: psycopg.Connection) -> Iterator[dict[str, TestUser]]:
    specs = {
        "owner_a": (TENANT_A, "owner"),
        "staff_a": (TENANT_A, "staff"),
        "cpa_a": (TENANT_A, "cpa"),
        "owner_b": (TENANT_B, "owner"),
        "no_membership": (None, None),
    }
    run = uuid.uuid4().hex[:8]
    created: list[str] = []
    result: dict[str, TestUser] = {}
    with httpx.Client(base_url=f"{settings.supabase_url}/auth/v1", timeout=10) as auth:
        try:
            for name, (tenant_id, app_role) in specs.items():
                email = f"it-{name.replace('_', '-')}-{run}@test.local"
                response = auth.post(
                    "/admin/users",
                    headers=_admin_headers(settings),
                    json={"email": email, "password": PASSWORD, "email_confirm": True},
                )
                response.raise_for_status()
                user_id = response.json()["id"]
                created.append(user_id)

                # The membership must exist before sign-in: the access token hook reads it.
                if tenant_id:
                    db.execute(
                        "insert into public.tenant_members (user_id, tenant_id, app_role) "
                        "values (%s, %s, %s)",
                        (user_id, tenant_id, app_role),
                    )

                token_response = auth.post(
                    "/token",
                    params={"grant_type": "password"},
                    headers={"apikey": settings.supabase_anon_key},
                    json={"email": email, "password": PASSWORD},
                )
                token_response.raise_for_status()
                token = token_response.json()["access_token"]
                result[name] = TestUser(name, user_id, email, token, tenant_id, app_role)
            yield result
        finally:
            for user_id in created:
                auth.delete(f"/admin/users/{user_id}", headers=_admin_headers(settings))


@pytest.fixture(scope="session")
def client(settings: Settings) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app, backend_options=LOOP_OPTIONS) as test_client:
        yield test_client


# Rows created by the tests carry the IT- prefix in one identifying column per table.
_TEST_ROWS = (
    ("products", "sku"),
    ("materials", "name"),
    ("customers", "first_name"),
    ("expenses", "description"),
)


@pytest.fixture(autouse=True)
def _cleanup_test_rows(db: psycopg.Connection) -> Iterator[None]:
    """Remove every row the tests created (IT- prefix), whatever the test did."""
    yield
    for table, column in _TEST_ROWS:
        db.execute(f"delete from public.{table} where {column} like %s", (f"{TEST_SKU_PREFIX}%",))


@pytest.fixture(scope="session", autouse=True)
def _cleanup_automation_events(db: psycopg.Connection) -> Iterator[None]:
    """Triggers write automation_events (e.g. stock_low); drop the ones this run produced."""
    started_at = db.execute("select clock_timestamp()").fetchone()[0]
    yield
    db.execute("delete from public.automation_events where created_at >= %s", (started_at,))


@pytest.fixture
def anyio_backend() -> tuple[str, dict]:
    return ("asyncio", LOOP_OPTIONS)
