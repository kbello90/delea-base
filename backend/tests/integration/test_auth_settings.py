"""The running local Auth server: public sign-up refused, email sign-in on."""

import uuid

import httpx
import pytest

from scripts.check_auth_settings import fetch_settings, settings_problems

pytestmark = pytest.mark.integration


def test_public_sign_up_is_refused(settings) -> None:
    response = httpx.post(
        f"{settings.supabase_url}/auth/v1/signup",
        headers={"apikey": settings.supabase_anon_key},
        json={"email": f"it-signup-{uuid.uuid4().hex[:8]}@test.local", "password": "long-enough-1"},
        timeout=10,
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "signup_disabled"


def test_the_settings_check_passes_locally(settings) -> None:
    assert (
        settings_problems(fetch_settings(settings.supabase_url, settings.supabase_anon_key)) == []
    )
