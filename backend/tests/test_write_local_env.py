"""write_local_env.py (CI writes backend/.env with it): the API gets Supabase's new keys, as on
the hosted projects. Tests the rendering only; nothing is written."""

from scripts.write_local_env import env_lines

STATUS = {
    "API_URL": "http://127.0.0.1:55321",
    "DB_URL": "postgresql://postgres:postgres@127.0.0.1:55322/postgres",
    "ANON_KEY": "eyJ.legacy.anon",
    "SERVICE_ROLE_KEY": "eyJ.legacy.service",
    "PUBLISHABLE_KEY": "sb_publishable_local",
    "SECRET_KEY": "sb_secret_local",
}


def test_the_api_gets_the_new_keys_and_its_own_login() -> None:
    env = dict(line.split("=", 1) for line in env_lines(STATUS)[1:])
    assert env["SUPABASE_ANON_KEY"] == "sb_publishable_local"
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "sb_secret_local"
    assert env["DATABASE_URL"].startswith("postgresql://delea_api:delea-api-local@127.0.0.1:")
    assert env["ADMIN_DATABASE_URL"] == STATUS["DB_URL"]
    assert "eyJ" not in "".join(env.values())
