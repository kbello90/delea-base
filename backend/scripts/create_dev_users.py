"""Create the three LOCAL dev users for tenant A (owner, staff, cpa) for manual testing.

    python backend/scripts/create_dev_users.py

Idempotent: an existing user keeps its id and gets its password reset to DEV_PASSWORD and its
tenant A role set again. Reads SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY and DATABASE_URL from
backend/.env (see write_local_env.py) and refuses anything that isn't the local stack.

The password is public and local-only. The integration tests don't use these users; they
create and delete their own.
"""

import asyncio
import sys
from pathlib import Path

import psycopg

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.core.auth_admin import AuthAdmin  # noqa: E402
from app.core.config import Settings  # noqa: E402

TENANT_A = "11111111-1111-1111-1111-111111111111"  # "DELEA Demo" in supabase/seed.sql
DEV_PASSWORD = "delea-local-dev"
DEV_USERS = (
    ("owner@delea.local", "owner"),
    ("staff@delea.local", "staff"),
    ("cpa@delea.local", "cpa"),
)
LOCAL_URLS = {
    "SUPABASE_URL": ("http://127.0.0.1:", "http://localhost:"),
    "DATABASE_URL": (
        "postgresql://postgres:postgres@127.0.0.1:",
        "postgresql://postgres:postgres@localhost:",
    ),
}


def _local_settings() -> Settings | None:
    settings = Settings(_env_file=BACKEND / ".env")
    values = {
        "SUPABASE_URL": settings.supabase_url,
        "DATABASE_URL": settings.database_url,
        "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        names = ", ".join(missing)
        print(f"backend/.env is missing {names}; see write_local_env.py", file=sys.stderr)
        return None
    for name, prefixes in LOCAL_URLS.items():
        if not values[name].startswith(prefixes):
            print(f"{name} is not a local stack URL; refusing to create users", file=sys.stderr)
            return None
    return settings


async def _ensure_users(settings: Settings) -> list[tuple[str, str, str, str]]:
    """(email, role, user_id, action) for each dev user, created or with its password reset."""
    admin = AuthAdmin(settings.supabase_url, settings.supabase_service_role_key)
    result = []
    for email, role in DEV_USERS:
        existed = await admin.find_user_id(email) is not None
        user_id = await admin.ensure_user(email, DEV_PASSWORD)
        result.append((email, role, user_id, "updated" if existed else "created"))
    return result


def main() -> int:
    settings = _local_settings()
    if settings is None:
        return 1

    users = asyncio.run(_ensure_users(settings))
    with psycopg.connect(settings.database_url, autocommit=True) as db:
        for email, role, user_id, action in users:
            # The access token hook reads tenant_members, so the role shows up at next sign-in.
            db.execute(
                "insert into public.tenant_members (user_id, tenant_id, app_role) "
                "values (%s, %s, %s) "
                "on conflict (user_id, tenant_id) do update set app_role = excluded.app_role",
                (user_id, TENANT_A, role),
            )
            print(f"{action} {email} ({role}, tenant A)")
    print(f"password for all three (local only): {DEV_PASSWORD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
