"""CORS: exact allowed origins only (CORS_ORIGINS), no wildcard. No database needed."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import app

client = TestClient(app)


def preflight(origin: str):
    return client.options(
        "/api/v1/products",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://localhost:5174"])
def test_preflight_from_allowed_origin_succeeds(origin: str) -> None:
    response = preflight(origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed_methods = response.headers["access-control-allow-methods"]
    for method in ("GET", "POST", "PATCH", "DELETE", "OPTIONS"):
        assert method in allowed_methods
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed_headers and "content-type" in allowed_headers


def test_preflight_from_disallowed_origin_gets_no_cors_headers() -> None:
    response = preflight("https://evil.example")
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_from_disallowed_origin_gets_no_cors_headers() -> None:
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_origins_setting_parses_comma_separated_list() -> None:
    settings = Settings(cors_origins="https://a.example, https://b.example ,", _env_file=None)
    assert settings.cors_origin_list == ["https://a.example", "https://b.example"]


def test_wildcard_origin_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(cors_origins="*", _env_file=None)
