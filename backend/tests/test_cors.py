"""CORS: exact allowed origins only (CORS_ORIGINS), no wildcard. No database needed."""

import pytest
from fastapi.routing import APIRoute
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


def _walk(routes, prefix: str = ""):
    """Every APIRoute with its full path. FastAPI 0.157 keeps include_router() as a nested
    included-router entry (original_router + include_context) instead of copying the routes
    into app.routes, so follow those too (duck-typed: the class is private)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router") and hasattr(route, "include_context"):
            yield from _walk(route.original_router.routes, prefix + route.include_context.prefix)


def _route_methods() -> list[tuple[str, str]]:
    """(method, path) for one route per HTTP method any API route uses, read from app.routes."""
    found: dict[str, str] = {}
    for path, route in _walk(app.routes):
        for method in route.methods:
            found.setdefault(method, path)
    return sorted(found.items())


def test_route_methods_are_collected() -> None:
    # Guards the parametrize below against silently collecting nothing.
    assert {"GET", "POST", "PUT", "PATCH", "DELETE"} <= {m for m, _ in _route_methods()}


@pytest.mark.parametrize(("method", "path"), _route_methods())
def test_preflight_succeeds_for_every_method_a_route_uses(method: str, path: str) -> None:
    # A real browser preflight with the headers the admin app sends (the API client sets
    # Authorization; openapi-fetch sets Content-Type on JSON bodies). Path parameters need no
    # real value: CORS answers the preflight before routing.
    response = client.options(
        path,
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "authorization, content-type",
        },
    )
    assert response.status_code == 200, f"{method} {path}: {response.status_code} {response.text}"
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert method in response.headers["access-control-allow-methods"]
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
