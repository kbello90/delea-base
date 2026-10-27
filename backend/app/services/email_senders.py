"""Who delivers email (TRD §6.16): Resend, or the local mail catcher.

One small interface so the provider can change: `send(message)` returns the provider's id, or
raises TemporaryFailure (retried later) or PermanentFailure (never retried). Resend gets the
outbox row id as its Idempotency-Key: a retry after a crash between sending and recording
"sent" is not delivered twice (Resend keeps keys 24 hours). Keys are never logged: errors name
the status, never the request.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"
# Addresses that can't receive mail on the internet (RFC 2606 / 6761): the showcase's and the
# tests' accounts. Resend would only bounce them, hurting the domain's reputation.
UNDELIVERABLE_SUFFIXES = (
    ".test",
    ".example",
    ".invalid",
    ".localhost",
    "@example.com",
    "@example.org",
    "@example.net",
)


class TemporaryFailure(Exception):
    """Try again later (rate limit, provider down, timeout)."""


class PermanentFailure(Exception):
    """Never retried (refused address, invalid message)."""


@dataclass(frozen=True)
class OutgoingEmail:
    id: str  # the outbox row id: the idempotency key
    sender: str  # "Shop Name <notifications@mail.example.com>"
    recipient: str
    reply_to: str | None
    subject: str
    html: str
    text: str


class EmailSender(Protocol):
    name: str

    async def send(self, email: OutgoingEmail) -> str: ...


class ResendSender:
    name = "resend"

    def __init__(self, api_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=15, transport=transport
        )

    async def send(self, email: OutgoingEmail) -> str:
        if email.recipient.lower().endswith(UNDELIVERABLE_SUFFIXES):
            raise PermanentFailure("a reserved test address; not sent through Resend")
        body = {
            "from": email.sender,
            "to": [email.recipient],
            "subject": email.subject,
            "html": email.html,
            "text": email.text,
        }
        if email.reply_to:
            body["reply_to"] = email.reply_to
        try:
            response = await self._client.post(
                RESEND_URL, json=body, headers={"Idempotency-Key": f"delea-{email.id}"}
            )
        except httpx.HTTPError as exc:
            raise TemporaryFailure(f"Resend unreachable: {type(exc).__name__}") from exc
        if response.status_code in (200, 201):
            return str(response.json().get("id", ""))
        # A bad or revoked key, a rate or daily limit, or Resend down: the email waits.
        if response.status_code in (401, 403, 408, 409, 429) or response.status_code >= 500:
            raise TemporaryFailure(f"Resend answered {response.status_code}")
        raise PermanentFailure(f"Resend refused it ({response.status_code})")


class MailpitSender:
    """The local mail catcher from the Supabase stack (local/test only): nothing leaves the
    machine; every email is visible at its web page."""

    name = "local mail catcher"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10, transport=transport)

    async def send(self, email: OutgoingEmail) -> str:
        name, _, address = email.sender.rpartition(" <")
        body = {
            "From": {"Name": name, "Email": address.rstrip(">")},
            "To": [{"Email": email.recipient}],
            "Subject": email.subject,
            "HTML": email.html,
            "Text": email.text,
            "Headers": {"X-Delea-Message": email.id},
        }
        if email.reply_to:
            body["ReplyTo"] = [{"Email": email.reply_to}]
        try:
            response = await self._client.post("/api/v1/send", json=body)
        except httpx.HTTPError as exc:
            raise TemporaryFailure(f"mail catcher unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise TemporaryFailure(f"mail catcher answered {response.status_code}")
        return str(response.json().get("ID", ""))


class EmailSettingsError(RuntimeError):
    """The API and the worker refuse to start with these email settings."""


def provider_name(settings: Settings) -> str:
    """resend, mailpit or off, from EMAIL_PROVIDER (auto by default)."""
    chosen = settings.email_provider.strip().lower() or "auto"
    if chosen == "auto":
        if settings.resend_api_key.strip():
            return "resend"
        return "mailpit" if settings.is_dev_environment else "off"
    return chosen


def check_email_settings(settings: Settings) -> None:
    chosen = settings.email_provider.strip().lower() or "auto"
    if chosen not in ("auto", "resend", "mailpit", "off"):
        raise EmailSettingsError("EMAIL_PROVIDER must be auto, resend, mailpit or off.")
    if chosen == "mailpit" and not settings.is_dev_environment:
        raise EmailSettingsError("EMAIL_PROVIDER=mailpit is for ENVIRONMENT=local/test only.")
    if chosen == "resend" and not settings.resend_api_key.strip():
        raise EmailSettingsError("EMAIL_PROVIDER=resend needs RESEND_API_KEY.")


def build_sender(settings: Settings) -> EmailSender | None:
    """The configured sender, or None when email is off."""
    name = provider_name(settings)
    if name == "resend":
        return ResendSender(settings.resend_api_key.strip())
    if name == "mailpit":
        return MailpitSender(settings.mailpit_url)
    return None


def from_address(from_name: str, settings: Settings) -> str:
    return f"{from_name} <notifications@{settings.email_from_domain.strip()}>"
