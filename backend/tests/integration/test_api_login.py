"""The API's own login is the least-privilege delea_api (TRD §6.6, migration 20261103000100).

These sign in with DATABASE_URL itself, so SET ROLE is decided by delea_api as the session
user (pgTAP can't: its session user is postgres). The rest of the integration suite runs the
API through the same login, which shows it is enough for every endpoint."""

import os

import psycopg
import pytest
from psycopg import errors

from app.core.config import Settings

pytestmark = pytest.mark.integration

ALLOWED = (
    "authenticated",
    "ai_readonly",
    "storefront_reader",
    "storefront_writer",
    "payments_writer",
    "email_worker",
)


@pytest.fixture
def api(settings: Settings):
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        user = conn.execute("select session_user").fetchone()[0]
        if user != "delea_api":
            message = (
                f"DATABASE_URL signs in as {user}, not delea_api: run "
                "scripts/write_local_env.py, or set DATABASE_URL to the delea_api login"
            )
            if os.environ.get("DELEA_REQUIRE_INTEGRATION") == "1":
                pytest.fail(message, pytrace=False)
            pytest.skip(message)
        yield conn


def test_the_api_signs_in_as_delea_api_without_privileges(api: psycopg.Connection) -> None:
    flags = api.execute(
        "select rolsuper, rolbypassrls, rolinherit, rolcreaterole from pg_roles "
        "where rolname = session_user"
    ).fetchone()
    assert flags == (False, False, False, False)
    for table in ("public.tenants", "public.invoices", "auth.users", "public.payments"):
        with pytest.raises(errors.InsufficientPrivilege), api.transaction():
            api.execute(f"select 1 from {table} limit 1")


@pytest.mark.parametrize("role", ALLOWED)
def test_it_switches_into_each_role_the_api_uses(api: psycopg.Connection, role: str) -> None:
    with api.transaction():
        api.execute(f"set local role {role}")
        assert api.execute("select current_user").fetchone()[0] == role


@pytest.mark.parametrize(
    "role", ["postgres", "service_role", "supabase_admin", "authenticator", "anon"]
)
def test_it_can_never_become_anyone_else(api: psycopg.Connection, role: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege), api.transaction():
        api.execute(f"set local role {role}")


def test_it_cant_set_is_demo_even_as_authenticated(api: psycopg.Connection) -> None:
    """Without verified claims RLS shows no tenant, and the is_demo guard refuses every app
    role anyway: refused outright or no row changed, never a change."""
    with api.transaction(force_rollback=True):
        api.execute("set local role authenticated")
        try:
            cur = api.execute("update public.tenants set is_demo = false where is_demo")
        except psycopg.Error:
            return
        assert cur.rowcount == 0
