"""The running local stack is on the hosted Postgres major version. A stack created before
supabase/config.toml moved to 17 keeps its old data volume and fails here: recreate it
(README: "Local stack")."""

import psycopg
import pytest

from tests.test_postgres_version import HOSTED_MAJOR_VERSION

pytestmark = pytest.mark.integration


def test_the_running_database_is_on_the_hosted_major_version(settings) -> None:
    with psycopg.connect(settings.admin_dsn) as conn:
        version = conn.execute("show server_version_num").fetchone()[0]
    assert int(version) // 10000 == HOSTED_MAJOR_VERSION
