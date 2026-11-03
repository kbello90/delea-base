"""The integration tests' guard (tests/integration/support.py remote_targets): they run against
the local Supabase stack only, because their cleanup changes every shop's data."""

import pytest

from tests.integration.support import remote_targets

LOCAL_DB = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
LOCAL_API = "http://127.0.0.1:54321"


@pytest.mark.parametrize(
    ("database_url", "supabase_url"),
    [
        (LOCAL_DB, LOCAL_API),
        ("postgresql://postgres:postgres@localhost:54322/postgres", "http://localhost:54321"),
        ("postgresql://postgres:postgres@[::1]:54322/postgres", "http://[::1]:54321"),
        (None, None),  # not configured: the fixtures skip with the start hint instead
    ],
)
def test_the_local_stack_is_allowed(database_url: str | None, supabase_url: str | None) -> None:
    assert remote_targets(database_url, supabase_url) == []


@pytest.mark.parametrize(
    ("database_url", "supabase_url", "refused"),
    [
        (
            "postgresql://postgres.abcd:pw@aws-0-us-east-1.pooler.supabase.com:6543/postgres",
            "https://abcd.supabase.co",
            ["DATABASE_URL", "SUPABASE_URL"],
        ),
        (LOCAL_DB, "https://abcd.supabase.co", ["SUPABASE_URL"]),
        ("postgresql://postgres:pw@db.abcd.supabase.co:5432/postgres", LOCAL_API, ["DATABASE_URL"]),
        # Look-alike hosts are not this machine.
        (
            "postgresql://postgres:pw@127.0.0.1.example.com:5432/postgres",
            LOCAL_API,
            ["DATABASE_URL"],
        ),
        (LOCAL_DB, "http://localhost.example.com:54321", ["SUPABASE_URL"]),
    ],
)
def test_anything_else_is_refused_by_name(
    database_url: str, supabase_url: str, refused: list[str]
) -> None:
    names = remote_targets(database_url, supabase_url)
    assert names == refused
    assert all("pw" not in n and "supabase.co" not in n for n in names)  # never the URL
