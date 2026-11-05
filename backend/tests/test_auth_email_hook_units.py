"""Send Email Hook pieces that need no database (hosted-demo plan step 4, TRD §6.6): the
Standard Webhooks signature check, the two Auth emails, and the provider call (idempotency key,
short timeout). The endpoint itself is tested against the local stack in
tests/integration/test_auth_email_hook.py."""

import base64
import json

import httpx
import pytest

from app.api.v1.auth_email_hook import PROVIDER_TIMEOUT_SECONDS, render_auth_email
from app.core.config import Settings
from app.core.standard_webhooks import WebhookRefused, secret_key, sign, verify
from app.services.email_senders import MailpitSender, OutgoingEmail, ResendSender, build_sender
from app.services.email_templates import auth_invite, auth_recovery

KEY = b"0123456789abcdef0123456789abcdef"
SECRET = "v1,whsec_" + base64.b64encode(KEY).decode()
NOW = 1_800_000_000
BODY = b'{"user":{"id":"x"}}'


def _headers(body: bytes = BODY, at: int = NOW, key: bytes = KEY, wid: str = "msg_1") -> dict:
    return {
        "webhook-id": wid,
        "webhook-timestamp": str(at),
        "webhook-signature": sign(key, wid, str(at), body),
    }


def _verify(headers: dict, body: bytes = BODY, secret: str = SECRET) -> str:
    return verify(secret, headers, body, now=lambda: NOW)


# ---------------------------------------------------------------------------
# Signature
# ---------------------------------------------------------------------------
def test_a_valid_signature_returns_the_webhook_id() -> None:
    assert _verify(_headers()) == "msg_1"


def test_both_secret_formats_supabase_shows_are_accepted() -> None:
    assert secret_key(SECRET) == KEY
    assert secret_key("whsec_" + base64.b64encode(KEY).decode()) == KEY
    assert _verify(_headers(), secret="  " + SECRET + "\n") == "msg_1"


def test_a_rotated_secret_list_passes_when_any_signature_matches() -> None:
    headers = _headers()
    other = sign(b"another-secret-of-32-bytes-long!", "msg_1", str(NOW), BODY)
    headers["webhook-signature"] = f"{other} {headers['webhook-signature']}"
    assert _verify(headers) == "msg_1"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (
            lambda h: h.update({"webhook-signature": sign(b"x" * 32, "msg_1", str(NOW), BODY)}),
            "no valid",
        ),
        (lambda h: h.update({"webhook-id": "msg_2"}), "no valid"),
        (lambda h: h.pop("webhook-signature"), "missing"),
        (lambda h: h.pop("webhook-id"), "missing"),
        (lambda h: h.pop("webhook-timestamp"), "missing"),
        (lambda h: h.update({"webhook-timestamp": "soon"}), "malformed"),
        (lambda h: h.update({"webhook-signature": "v1,"}), "no valid"),
    ],
)
def test_a_wrong_or_missing_header_is_refused(change, reason) -> None:
    headers = _headers()
    change(headers)
    with pytest.raises(WebhookRefused, match=reason):
        _verify(headers)


def test_a_changed_body_is_refused() -> None:
    with pytest.raises(WebhookRefused, match="no valid"):
        _verify(_headers(), body=BODY.replace(b'"x"', b'"y"'))


@pytest.mark.parametrize("offset", [301, -301, 86_400])
def test_a_timestamp_more_than_five_minutes_off_is_refused(offset: int) -> None:
    with pytest.raises(WebhookRefused, match="tolerance"):
        _verify(_headers(at=NOW + offset))


def test_a_timestamp_within_five_minutes_passes() -> None:
    assert _verify(_headers(at=NOW - 299)) == "msg_1"
    assert _verify(_headers(at=NOW + 299)) == "msg_1"


@pytest.mark.parametrize(
    "bad", ["v1,whsec_not base64!", "v1,whsec_" + base64.b64encode(b"short").decode()]
)
def test_a_malformed_secret_is_a_configuration_error_not_a_pass(bad: str) -> None:
    with pytest.raises(ValueError):
        _verify(_headers(), secret=bad)


def test_refusal_reasons_never_contain_the_secret_or_a_signature() -> None:
    headers = _headers(key=b"y" * 32)
    with pytest.raises(WebhookRefused) as caught:
        _verify(headers)
    assert SECRET not in str(caught.value) and headers["webhook-signature"] not in str(caught.value)


# ---------------------------------------------------------------------------
# The two emails
# ---------------------------------------------------------------------------
def test_the_invite_names_the_shop_and_links_to_the_accept_page(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.auth_email_hook.get_settings",
        lambda: Settings(admin_app_url="https://app.deleajewelry.com/"),
    )
    email = render_auth_email("invite", "Aurora Gems", "pkce_ab+c/d=")
    link = "https://app.deleajewelry.com/accept-invite?token_hash=pkce_ab%2Bc%2Fd%3D&type=invite"
    assert email.subject == "You're invited to Aurora Gems on DELEA"
    assert link in email.text
    assert link.replace("&", "&amp;") in email.html
    assert "expires in 24 hours" in email.text
    assert "Accept the invite" in email.html


def test_the_reset_links_to_the_reset_page(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.auth_email_hook.get_settings",
        lambda: Settings(admin_app_url="https://app.deleajewelry.com"),
    )
    email = render_auth_email("recovery", None, "abc123")
    assert email.subject == "Reset your DELEA password"
    assert (
        "https://app.deleajewelry.com/reset-password?token_hash=abc123&type=recovery" in email.text
    )
    assert "your password hasn't changed" in email.text


def test_a_shop_name_is_escaped_and_cannot_break_the_subject() -> None:
    email = auth_invite("Gems <b>&</b>\r\nBcc: x@evil.test", "https://app.example.test/x")
    assert "\n" not in email.subject and "\r" not in email.subject
    assert "<b>&</b>" not in email.html and "&lt;b&gt;&amp;&lt;/b&gt;" in email.html


def test_both_emails_fit_a_phone_and_have_a_plain_text_part() -> None:
    for email in (
        auth_invite("Aurora Gems", "https://a.test/x"),
        auth_recovery("https://a.test/y"),
    ):
        assert 'name="viewport"' in email.html and "max-width:560px" in email.html
        assert email.text.strip() and "DELEA" in email.text


# ---------------------------------------------------------------------------
# The provider call
# ---------------------------------------------------------------------------
def _email() -> OutgoingEmail:
    return OutgoingEmail(
        id="auth-msg_9",
        sender="DELEA <notifications@mail.deleajewelry.com>",
        recipient="owner@aurora-gems.com",
        reply_to=None,
        subject="Reset your DELEA password",
        html="<p>x</p>",
        text="x",
    )


@pytest.mark.anyio
async def test_resend_gets_the_webhook_id_as_its_idempotency_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": "re_1"})

    sender = ResendSender("re_test", transport=httpx.MockTransport(handler))
    assert await sender.send(_email()) == "re_1"
    await sender.aclose()
    assert seen[0].headers["Idempotency-Key"] == "delea-auth-msg_9"
    assert json.loads(seen[0].content)["from"] == "DELEA <notifications@mail.deleajewelry.com>"


def test_the_hook_builds_its_sender_with_the_short_timeout() -> None:
    resend = build_sender(
        Settings(environment="demo", email_provider="resend", resend_api_key="re_x"),
        timeout=PROVIDER_TIMEOUT_SECONDS,
    )
    mailpit = build_sender(Settings(environment="local", email_provider="mailpit"), timeout=3)
    assert isinstance(resend, ResendSender) and isinstance(mailpit, MailpitSender)
    assert resend._client.timeout.connect == PROVIDER_TIMEOUT_SECONDS < 5
    assert mailpit._client.timeout.connect == 3
    default = build_sender(
        Settings(environment="demo", email_provider="resend", resend_api_key="re_x")
    )
    assert default._client.timeout.connect == 15  # the worker keeps its own


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
