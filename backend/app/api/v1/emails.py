"""Email settings, the reply-to confirmation and the provider webhook (Slice D, TRD §6.16).

Owner only, under /settings/emails: the owner alerts and customer emails on or off (with an
intro of the owner's own for each customer email), the sender name, the reply-to address (used
only once confirmed through the link emailed to it), a preview of each customer email with a
made-up order, a test sent to the owner's own sign-in email, and the shop's latest emails.
Emails are queued through email_worker (email_enqueue enforces who may receive what); the
worker sends them.

Public, under /email: the confirmation link's endpoint (no sign-in: whoever owns the address
confirms it) and Resend's webhook for bounces and complaints, signed with Svix's scheme.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.api.deps import CurrentClaims, DbConn, get_pool, require_role
from app.core import rate_limit
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.core.storefront_hosts import storefront_url
from app.db.session import email_session, service_session
from app.models.emails import (
    CustomerEmail,
    CustomerEmails,
    EmailLogEntry,
    EmailPreviewOut,
    EmailPreviewRequest,
    EmailSettingsOut,
    EmailSettingsUpdate,
    OwnerAlerts,
    ReplyToConfirm,
    ReplyToConfirmed,
    ReplyToRequest,
    TestEmailOut,
    TestEmailRequest,
)
from app.services import email_templates as templates
from app.services.email_senders import from_address, provider_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/emails", tags=["settings"])
public_router = APIRouter(prefix="/email", tags=["email"])

OwnerOnly = Depends(require_role("owner"))
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]

PROVIDER_LABEL = {"resend": "resend", "mailpit": "local", "off": "off"}
WEBHOOK_TOLERANCE = 300


CUSTOMER_COLUMNS = ", ".join(f"es.customer_{t}, es.intro_{t}" for t in templates.CUSTOMER_TEMPLATES)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _shop(conn, tenant_id: str) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select coalesce(b.business_name, t.name) as business_name, b.primary_color, "
            "t.is_demo, es.alert_new_order, es.alert_low_stock, es.alert_balance_due, "
            f"{CUSTOMER_COLUMNS}, "
            "es.sender_name, case when es.reply_to_confirmed_at is not null "
            "then es.reply_to_email end as reply_to, es.reply_to_pending, "
            "ss.contact_email, (select d.domain from public.tenant_domains d "
            "where d.tenant_id = t.id and d.kind = 'storefront' order by d.domain limit 1) "
            "as storefront_domain "
            "from public.tenants t left join public.brand_config b on b.tenant_id = t.id "
            "left join public.email_settings es on es.tenant_id = t.id "
            "left join public.storefront_settings ss on ss.tenant_id = t.id where t.id = %s",
            (tenant_id,),
        )
        row = await cur.fetchone()
    assert row is not None  # the caller's own tenant
    return row


async def _out(conn, tenant_id: str) -> EmailSettingsOut:
    settings = get_settings()
    shop = await _shop(conn, tenant_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select created_at, kind, template, recipient, subject, status, last_error "
            "from public.email_messages where tenant_id = %s "
            "order by created_at desc limit 20",
            (tenant_id,),
        )
        recent = await cur.fetchall()
        await cur.execute(
            "select exists (select 1 from public.email_messages where tenant_id = %s "
            "and status in ('queued', 'sending') and created_at < now() - interval '15 minutes')"
            " as delayed",
            (tenant_id,),
        )
        delayed = (await cur.fetchone())["delayed"]
    name = shop["sender_name"] or shop["business_name"]
    return EmailSettingsOut(
        provider=PROVIDER_LABEL[provider_name(settings)],
        from_address=from_address(name, settings),
        sender_name=shop["sender_name"],
        reply_to=shop["reply_to"],
        reply_to_pending=shop["reply_to_pending"],
        alerts=OwnerAlerts(
            new_order=shop["alert_new_order"] is not False,
            low_stock=shop["alert_low_stock"] is not False,
            balance_due=shop["alert_balance_due"] is not False,
        ),
        customer=CustomerEmails(
            **{
                t: CustomerEmail(
                    enabled=shop[f"customer_{t}"] is not False, intro=shop[f"intro_{t}"]
                )
                for t in templates.CUSTOMER_TEMPLATES
            }
        ),
        delayed=delayed,
        recent=[EmailLogEntry.model_validate(r) for r in recent],
    )


@router.get("", response_model=EmailSettingsOut, dependencies=[OwnerOnly])
async def email_settings(claims: CurrentClaims, conn: DbConn) -> EmailSettingsOut:
    return await _out(conn, claims.tenant_id)


@router.patch("", response_model=EmailSettingsOut, dependencies=[OwnerOnly])
async def update_email_settings(
    body: EmailSettingsUpdate, claims: CurrentClaims, conn: DbConn
) -> EmailSettingsOut:
    """The alerts and customer emails on or off, the intros, and the sender name (null: the
    business name). Only what is sent changes; the column names below are fixed, never taken
    from the request."""
    changed: dict[str, Any] = {}
    if "alerts" in body.model_fields_set and body.alerts is not None:
        changed |= {
            "alert_new_order": body.alerts.new_order,
            "alert_low_stock": body.alerts.low_stock,
            "alert_balance_due": body.alerts.balance_due,
        }
    if "customer" in body.model_fields_set and body.customer is not None:
        for t in templates.CUSTOMER_TEMPLATES:
            email: CustomerEmail = getattr(body.customer, t)
            changed[f"customer_{t}"] = email.enabled
            changed[f"intro_{t}"] = email.intro or None
    if "sender_name" in body.model_fields_set:
        changed["sender_name"] = body.sender_name
    if changed:
        columns = ", ".join(changed)
        placeholders = ", ".join(f"%({k})s" for k in changed)
        updates = ", ".join(f"{k} = excluded.{k}" for k in changed)
        await conn.execute(
            f"insert into public.email_settings (tenant_id, {columns}) "
            f"values (%(tenant_id)s, {placeholders}) "
            f"on conflict (tenant_id) do update set {updates}",
            {**changed, "tenant_id": claims.tenant_id},
        )
    return await _out(conn, claims.tenant_id)


@router.post("/reply-to", response_model=EmailSettingsOut, dependencies=[OwnerOnly])
async def request_reply_to(
    body: ReplyToRequest, claims: CurrentClaims, conn: DbConn, pool: Pool
) -> EmailSettingsOut:
    """Emails a one-time confirmation link (24 hours) to the address. Until it is confirmed,
    customers' emails carry no reply-to. A demo shop can only confirm its own owner's email."""
    if rate_limit.reply_to_requests.hit(claims.tenant_id) is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try later.")
    token = secrets.token_urlsafe(32)
    await conn.execute("select public.email_request_reply_to(%s, %s)", (body.email, _hash(token)))
    shop = await _shop(conn, claims.tenant_id)
    link = f"{get_settings().admin_app_url.rstrip('/')}/confirm-reply-to?token={token}"
    content = templates.reply_to_confirmation(shop["business_name"], shop["primary_color"], link)
    async with email_session(pool) as writer:
        await writer.execute(
            "select public.email_enqueue(%s, 'reply_to_confirmation', 'reply_to_confirmation', "
            "%s, %s, %s, %s, %s)",
            (
                claims.tenant_id,
                f"reply_to:{_hash(token)}",
                body.email,
                content.subject,
                content.html,
                content.text,
            ),
        )
    return await _out(conn, claims.tenant_id)


def _sample(shop: dict[str, Any], template: str, intro: str | None) -> templates.Rendered:
    """A customer email about a made-up order, in the shop's colours and with its footer."""
    website = (
        storefront_url(shop["storefront_domain"], get_settings().is_dev_environment)
        if shop["storefront_domain"]
        else None
    )
    order = templates.sample_order(
        shop["business_name"],
        shop["primary_color"],
        website,
        shop["contact_email"] or shop["reply_to"],
        intro,
    )
    # The made-up order at the moment each email is about: just placed, after a $50 deposit,
    # or paid in full.
    if template == "order_received":
        order = replace(order, paid_cents=0, balance_cents=order.total_cents)
    elif template in ("payment_received", "in_production", "shipped"):
        order = replace(order, paid_cents=order.total_cents, balance_cents=0)
    return templates.customer_email(template, order, 5000)


@router.post("/preview", response_model=EmailPreviewOut, dependencies=[OwnerOnly])
async def preview(
    body: EmailPreviewRequest, claims: CurrentClaims, conn: DbConn
) -> EmailPreviewOut:
    """Renders a customer email with a made-up order (nothing is queued or sent). The intro
    is the one in the request, so unsaved text can be previewed."""
    shop = await _shop(conn, claims.tenant_id)
    content = _sample(shop, body.template, body.intro or None)
    return EmailPreviewOut(subject=content.subject, html=content.html, text=content.text)


@router.post("/test", response_model=TestEmailOut, dependencies=[OwnerOnly])
async def send_test(
    claims: CurrentClaims, conn: DbConn, pool: Pool, body: TestEmailRequest | None = None
) -> TestEmailOut:
    """A test to the signed-in owner's own sign-in email, and nowhere else (10 a day): the
    plain test, or one customer email about a made-up order (with the saved intro). Never to
    a customer."""
    async with service_session(pool) as service:
        cur = await service.execute(
            "select u.email from auth.users u join public.tenant_members m on m.user_id = u.id "
            "where u.id = %s and m.tenant_id = %s and m.app_role = 'owner'",
            (claims.sub, claims.tenant_id),
        )
        row = await cur.fetchone()
    if row is None or not row[0]:
        raise ConflictError("Your account has no email address to send a test to.")
    shop = await _shop(conn, claims.tenant_id)
    template = body.template if body else None
    if template:
        sample = _sample(shop, template, shop[f"intro_{template}"])
        content = templates.Rendered(
            subject=f"[Test] {sample.subject}", html=sample.html, text=sample.text
        )
    else:
        content = templates.test_email(shop["business_name"], shop["primary_color"])
    async with email_session(pool) as writer:
        cur = await writer.execute(
            "select public.email_enqueue(%s, 'test', %s, %s, %s, %s, %s, %s)",
            (
                claims.tenant_id,
                template or "test",
                f"test:{secrets.token_hex(8)}",
                row[0],
                content.subject,
                content.html,
                content.text,
            ),
        )
        (message_id,) = await cur.fetchone()
    cur = await conn.execute(
        "select status from public.email_messages where id = %s and tenant_id = %s",
        (message_id, claims.tenant_id),
    )
    (state,) = await cur.fetchone()
    return TestEmailOut(sent_to=row[0].lower(), status=state)


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
@public_router.post("/confirm-reply-to", response_model=ReplyToConfirmed)
async def confirm_reply_to(body: ReplyToConfirm, request: Request, pool: Pool) -> ReplyToConfirmed:
    client = request.client.host if request.client else "unknown"
    if rate_limit.reply_to_confirmations.hit(client) is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try later.")
    async with email_session(pool) as conn:
        cur = await conn.execute("select public.email_confirm_reply_to(%s)", (_hash(body.token),))
        (shop,) = await cur.fetchone()
    if shop is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This link has expired or was already used."
        )
    return ReplyToConfirmed(shop=shop)


def verify_svix(payload: bytes, headers, secret: str, now: float | None = None) -> str:
    """Resend signs webhooks with Svix's scheme: base64 HMAC-SHA256 of "<id>.<timestamp>.<body>"
    with the base64 key after "whsec_". Returns the message id; raises ValueError."""
    msg_id = headers.get("svix-id")
    stamp = headers.get("svix-timestamp")
    signatures = headers.get("svix-signature")
    if not (msg_id and stamp and signatures and stamp.isdigit()):
        raise ValueError("missing signature headers")
    if abs((now or time.time()) - int(stamp)) > WEBHOOK_TOLERANCE:
        raise ValueError("timestamp outside the tolerance")
    key = base64.b64decode(secret.removeprefix("whsec_"))
    expected = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + payload, hashlib.sha256).digest()
    ).decode()
    for candidate in signatures.split():
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return msg_id
    raise ValueError("no matching signature")


@public_router.post("/webhook")
async def email_webhook(request: Request, pool: Pool) -> dict[str, bool]:
    """Bounces and complaints suppress the address for every shop; each event once."""
    secret = get_settings().resend_webhook_secret.strip()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhooks aren't set up.")
    body = await request.body()
    if len(body) > 256_000:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Too large")
    try:
        msg_id = verify_svix(body, request.headers, secret)
        event = json.loads(body)
        kind = str(event["type"])
        recipients = (event.get("data") or {}).get("to") or []
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature or event") from None
    email = recipients[0] if isinstance(recipients, list) and recipients else None
    async with email_session(pool) as conn:
        cur = await conn.execute(
            "select public.email_record_provider_event(%s, %s, %s)",
            (msg_id, kind, email if isinstance(email, str) else None),
        )
        (outcome,) = await cur.fetchone()
    logger.info("email provider event %s (%s): %s", msg_id, kind, outcome)
    return {"received": True}
