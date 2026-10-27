"""Cloudflare Turnstile for the storefront checkout (TRD §6.11).

Fails closed: outside the local and test environments a missing TURNSTILE_SECRET_KEY means
no checkout at all (the store says checkout is off, and POST /public/orders answers 503), and
an unreachable or unreadable verification answer refuses the order. Only in local and test,
with no key set, is the check skipped (with a warning), so the storefront can be developed
without Cloudflare.

A token is accepted only when Cloudflare says it is valid, was issued for this shop's
hostname (so a token from another site can't be replayed here) and for the checkout action.
The hostname is not checked in local and test. An answer from Cloudflare's test keys
(metadata.result_with_testing_key) is accepted in local and test only, and refused anywhere
else, so test keys can never protect a real shop.
"""

import logging

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
ACTION = "checkout"
MAX_TOKEN_LENGTH = 2048
TIMEOUT_SECONDS = 5.0

# Tests replace this with an httpx.MockTransport; nothing else touches it.
transport: httpx.AsyncBaseTransport | None = None


class TurnstileUnavailable(Exception):
    """Checkout can't be protected right now (missing key, or Cloudflare unreachable)."""


def checkout_protected(settings: Settings) -> bool:
    """True when orders can be accepted: a secret key is set, or this is local/test."""
    return bool(settings.turnstile_secret_key) or settings.is_dev_environment


async def verify(
    token: str | None, *, remote_ip: str | None, hostname: str, settings: Settings
) -> bool:
    """Whether this token passes. Raises TurnstileUnavailable when the answer can't be known."""
    if not settings.turnstile_secret_key:
        if settings.is_dev_environment:
            logger.warning("TURNSTILE_SECRET_KEY is not set: skipping the check (local/test only)")
            return True
        raise TurnstileUnavailable("TURNSTILE_SECRET_KEY is not set")
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return False

    data = {"secret": settings.turnstile_secret_key, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(VERIFY_URL, data=data)
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Turnstile verification failed: %s", type(exc).__name__)
        raise TurnstileUnavailable("Turnstile could not be reached") from exc

    if not isinstance(result, dict) or result.get("success") is not True:
        return False
    metadata = result.get("metadata")
    testing_key = isinstance(metadata, dict) and metadata.get("result_with_testing_key") is True
    if testing_key:
        # Cloudflare's test keys (always pass, no action, hostname example.com): accepted in
        # local and test only, never anywhere else.
        return settings.is_dev_environment
    if result.get("action") != ACTION:
        return False
    if not settings.is_dev_environment and result.get("hostname") != hostname:
        return False
    return True
