"""Email pieces without the database: templates, senders (a fake HTTP transport; nothing leaves
the machine), provider choice, retry schedule, webhook signatures."""

import base64
import hashlib
import hmac
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from app.api.v1.emails import verify_svix
from app.core.config import Settings
from app.services import email_templates as t
from app.services.email_senders import (
    EmailSettingsError,
    MailpitSender,
    OutgoingEmail,
    PermanentFailure,
    ResendSender,
    TemporaryFailure,
    check_email_settings,
    from_address,
    provider_name,
)
from app.worker import MAX_ATTEMPTS, retry_at

EMAIL = OutgoingEmail(
    id="11111111-2222-3333-4444-555555555555",
    sender="Aurora Gems <notifications@mail.deleajewelry.com>",
    recipient="owner@shop.com",
    reply_to="shop@gmail.com",
    subject="Hello",
    html="<p>Hi</p>",
    text="Hi",
)


def transport(status: int, body: dict | None = None, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
def test_shop_and_shopper_text_is_escaped_everywhere() -> None:
    nasty = '<script>alert(1)</script> & "quotes"'
    invoice = {
        "order_ref": "W-ABCDEFGH",
        "items": 2,
        "total_cents": 16260,
        "currency": "USD",
        "customer_name": nasty,
        "payment_choice": "card",
    }
    out = t.new_order_alert(nasty, "#123456", invoice, 'https://admin.example/x"><b>')
    assert "<script>" not in out.html and "&lt;script&gt;" in out.html
    assert '"><b>' not in out.html
    assert "W-ABCDEFGH" in out.subject and "$162.60" in out.text and "card" in out.text


def test_a_colour_that_isnt_hex_falls_back() -> None:
    out = t.test_email("Shop", "red;background:url(x)")
    assert "url(x)" not in out.html and t.DEFAULT_COLOR in out.html


def test_owner_alerts_carry_no_street_or_phone() -> None:
    invoice = {
        "order_ref": "W-ABCDEFGH",
        "items": 1,
        "total_cents": 100,
        "currency": "USD",
        "customer_name": "Sarah Jenkins",
        "payment_choice": "bank_transfer",
        "ship_line1": "1 Main St",
        "phone": "555-0100",
    }
    out = t.new_order_alert("Shop", None, invoice, "https://a/x")
    assert "Main St" not in out.html + out.text and "555-0100" not in out.html + out.text


def test_money_and_the_digest() -> None:
    assert t.money(123456, "USD") == "$1,234.56"
    assert t.money(5, "GBP") == "£0.05"
    out = t.balance_due_digest(
        "Shop",
        None,
        "USD",
        [{"number": "W-1", "customer_name": "A", "balance_cents": 500, "due_date": "2026-10-01"}],
        date(2026, 10, 27),
        "https://a/invoices",
    )
    assert "1 order holds a deposit" in out.text and "$5.00" in out.text


def test_every_email_has_a_plain_text_version_and_fits_a_phone() -> None:
    for out in (
        t.test_email("Shop", None),
        t.reply_to_confirmation("Shop", None, "https://a/confirm?token=x"),
        t.low_stock_alert(
            "Shop", None, {"name": "Ring", "sku": "R1", "stock_qty": 1, "threshold": 3}, "https://a"
        ),
    ):
        assert out.text.strip() and "<" not in out.text
        assert 'name="viewport"' in out.html and "max-width:560px" in out.html


# ---------------------------------------------------------------------------
# Senders
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_resend_gets_the_row_id_as_its_idempotency_key() -> None:
    seen: list[httpx.Request] = []
    sender = ResendSender("re_test_not_real", transport(200, {"id": "email_1"}, seen))
    assert await sender.send(EMAIL) == "email_1"
    request = seen[0]
    assert request.headers["Idempotency-Key"] == f"delea-{EMAIL.id}"
    assert request.headers["Authorization"] == "Bearer re_test_not_real"
    body = request.read().decode()
    assert '"reply_to":"shop@gmail.com"' in body.replace(" ", "")
    assert '"from":"Aurora Gems <notifications@mail.deleajewelry.com>"' in body.replace(": ", ":")


@pytest.mark.anyio
@pytest.mark.parametrize("code", [401, 403, 409, 429, 500, 503])
async def test_resend_temporary_answers_are_retried(code: int) -> None:
    with pytest.raises(TemporaryFailure) as caught:
        await ResendSender("re_test_not_real", transport(code)).send(EMAIL)
    assert "re_test_not_real" not in str(caught.value)


@pytest.mark.anyio
async def test_resend_refusals_and_test_addresses_are_permanent() -> None:
    with pytest.raises(PermanentFailure):
        await ResendSender("k", transport(422)).send(EMAIL)
    for address in ("owner@showcase.delea.test", "x@example.com", "a@b.invalid"):
        with pytest.raises(PermanentFailure):
            await ResendSender("k", transport(200)).send(
                OutgoingEmail(**{**EMAIL.__dict__, "recipient": address})
            )


@pytest.mark.anyio
async def test_resend_unreachable_is_temporary() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(TemporaryFailure):
        await ResendSender("k", httpx.MockTransport(boom)).send(EMAIL)


@pytest.mark.anyio
async def test_the_local_mail_catcher_gets_the_same_email() -> None:
    seen: list[httpx.Request] = []
    sender = MailpitSender("http://mailpit", transport(200, {"ID": "m1"}, seen))
    assert await sender.send(EMAIL) == "m1"
    body = seen[0].read().decode()
    assert '"Name":"Aurora Gems"' in body.replace(": ", ":") and "shop@gmail.com" in body


def test_provider_choice() -> None:
    assert provider_name(Settings(environment="local")) == "mailpit"
    assert provider_name(Settings(environment="production")) == "off"
    assert provider_name(Settings(environment="production", resend_api_key="re_x")) == "resend"
    with pytest.raises(EmailSettingsError):
        check_email_settings(Settings(environment="production", email_provider="mailpit"))
    with pytest.raises(EmailSettingsError):
        check_email_settings(Settings(environment="local", email_provider="resend"))
    assert from_address("Aurora Gems", Settings(email_from_domain="mail.deleajewelry.com")) == (
        "Aurora Gems <notifications@mail.deleajewelry.com>"
    )


def test_retry_schedule() -> None:
    now = datetime(2026, 10, 27, tzinfo=UTC)
    assert retry_at(1, now) == now + timedelta(minutes=1)
    assert retry_at(5, now) == now + timedelta(hours=6)
    assert retry_at(MAX_ATTEMPTS, now) is None


# ---------------------------------------------------------------------------
# The provider webhook's signature (Svix's scheme, as Resend uses it)
# ---------------------------------------------------------------------------
SECRET = "whsec_" + base64.b64encode(b"a-made-up-local-key-for-tests").decode()


def svix_headers(body: bytes, stamp: int, secret: str = SECRET, msg_id: str = "msg_1") -> dict:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    sig = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"svix-id": msg_id, "svix-timestamp": str(stamp), "svix-signature": f"v1,{sig}"}


def test_svix_signatures() -> None:
    body = b'{"type":"email.bounced"}'
    now = 1_800_000_000
    assert verify_svix(body, svix_headers(body, now), SECRET, now=now) == "msg_1"
    with pytest.raises(ValueError):
        verify_svix(body + b" ", svix_headers(body, now), SECRET, now=now)
    with pytest.raises(ValueError):
        verify_svix(body, svix_headers(body, now - 400), SECRET, now=now)
    other = "whsec_" + base64.b64encode(b"another-key").decode()
    with pytest.raises(ValueError):
        verify_svix(body, svix_headers(body, now, other), SECRET, now=now)
