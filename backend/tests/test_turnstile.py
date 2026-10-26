"""Turnstile verification rules (app/core/turnstile.py). No network: a mock transport."""

import asyncio

import httpx
import pytest

from app.core import turnstile
from app.core.config import Settings


def _settings(environment: str, secret: str = "it-secret") -> Settings:
    return Settings(environment=environment, turnstile_secret_key=secret, _env_file=None)


def _verify(
    settings: Settings, answer, token: str | None = "tok", hostname: str = "shop.example.com"
):
    def handler(request: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    turnstile.transport = httpx.MockTransport(handler)
    try:
        return asyncio.run(
            turnstile.verify(token, remote_ip="203.0.113.7", hostname=hostname, settings=settings)
        )
    finally:
        turnstile.transport = None


GOOD = {"success": True, "action": "checkout", "hostname": "shop.example.com"}


def test_a_valid_token_for_this_shop_passes() -> None:
    assert _verify(_settings("production"), GOOD) is True


@pytest.mark.parametrize(
    "answer",
    [
        {**GOOD, "success": False},
        {**GOOD, "action": "login"},
        {**GOOD, "hostname": "another-site.example"},
        {"action": "checkout", "hostname": "shop.example.com"},
        ["not", "an", "object"],
    ],
)
def test_anything_else_fails(answer) -> None:
    assert _verify(_settings("production"), answer) is False


@pytest.mark.parametrize("token", [None, "", "x" * 2049])
def test_a_missing_or_oversized_token_fails_without_a_call(token) -> None:
    assert (
        _verify(_settings("production"), httpx.ConnectError("must not be called"), token=token)
        is False
    )


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        httpx.Response(500),
        httpx.Response(200, text="<html>"),
    ],
)
def test_cloudflare_unreachable_or_unreadable_fails_closed(answer) -> None:
    with pytest.raises(turnstile.TurnstileUnavailable):
        _verify(_settings("production"), answer)


def test_no_key_outside_development_fails_closed() -> None:
    settings = _settings("production", secret="")
    assert turnstile.checkout_protected(settings) is False
    with pytest.raises(turnstile.TurnstileUnavailable):
        _verify(settings, GOOD)


@pytest.mark.parametrize("environment", ["local", "test"])
def test_no_key_in_development_skips_the_check(environment: str) -> None:
    settings = _settings(environment, secret="")
    assert turnstile.checkout_protected(settings) is True
    assert _verify(settings, httpx.ConnectError("must not be called"), token=None) is True


def test_development_with_a_key_still_checks_but_not_the_hostname() -> None:
    # Cloudflare's test keys report their own hostname.
    assert _verify(_settings("local"), {**GOOD, "hostname": "example.com"}) is True
    assert _verify(_settings("local"), {**GOOD, "success": False}) is False
