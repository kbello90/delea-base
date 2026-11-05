"""The server key reaches Auth admin and Storage the way Supabase takes it (hosted-demo plan
step 5): the new secret key on `apikey` only, the legacy service_role JWT on both headers; and
the settings refuse the two new key kinds swapped. No network."""

import pytest
from pydantic import ValidationError

from app.core import storage
from app.core.auth_admin import AuthAdmin
from app.core.config import Settings
from app.core.supabase_keys import server_key_headers

SECRET = "sb_secret_abcDEF123_456"
PUBLISHABLE = "sb_publishable_abcDEF123_456"
LEGACY = "eyJhbGciOiJIUzI1NiJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.c2lnbmF0dXJl"


def test_a_secret_key_goes_on_apikey_only() -> None:
    assert server_key_headers(SECRET) == {"apikey": SECRET}


def test_the_legacy_service_role_key_goes_on_both_headers() -> None:
    assert server_key_headers(LEGACY) == {"apikey": LEGACY, "Authorization": f"Bearer {LEGACY}"}


def test_a_publishable_key_is_never_used_as_the_server_key() -> None:
    with pytest.raises(ValueError, match="not a server key"):
        server_key_headers(PUBLISHABLE)


@pytest.mark.parametrize(("key", "bearer"), [(SECRET, False), (LEGACY, True)])
def test_auth_admin_sends_the_key_the_right_way(key: str, bearer: bool) -> None:
    headers = AuthAdmin("https://ref.supabase.co", key)._client().headers
    assert headers["apikey"] == key
    assert ("authorization" in headers) is bearer


@pytest.mark.parametrize(("key", "bearer"), [(SECRET, False), (LEGACY, True)])
def test_storage_sends_the_key_the_right_way(monkeypatch, key: str, bearer: bool) -> None:
    monkeypatch.setattr(
        storage,
        "get_settings",
        lambda: Settings(supabase_url="https://ref.supabase.co", supabase_service_role_key=key),
    )
    headers = storage._headers()
    assert headers["apikey"] == key
    assert ("Authorization" in headers) is bearer


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("supabase_service_role_key", PUBLISHABLE, "SUPABASE_SERVICE_ROLE_KEY holds a publishable"),
        ("supabase_anon_key", SECRET, "SUPABASE_ANON_KEY holds a secret key"),
    ],
)
def test_swapped_keys_stop_the_app_without_showing_the_key(field, value, message) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(**{field: value})
    assert message in str(caught.value)
    assert value not in str(caught.value)


def test_the_right_keys_in_the_right_settings_are_accepted() -> None:
    settings = Settings(supabase_anon_key=PUBLISHABLE, supabase_service_role_key=SECRET)
    assert settings.supabase_service_role_key == SECRET


@pytest.mark.parametrize(
    "bad",
    [
        {"supabase_anon_key": SECRET},
        {"supabase_service_role_key": "@Microsoft.KeyVault(SecretUri=https://kv/x)"},
        {"forwarded_allow_ips": "*"},
        {"worker_health_port": "not-a-port"},
    ],
)
def test_no_settings_error_shows_any_setting_value(bad: dict) -> None:
    """pydantic prints part of the raw input by default: with the database URL last, the tail
    of its password. Settings hides the input in every error."""
    db = "postgresql://delea_api.ref:Tail0fThePassw0rd@pooler.example.test:5432/postgres"
    with pytest.raises(ValidationError) as caught:
        Settings(**{"database_url": db, **bad})
    text = str(caught.value)
    assert "input_value" not in text
    assert "Tail0fThePassw0rd" not in text and SECRET not in text
