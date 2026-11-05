"""Supabase Auth's Send Email Hook (hosted-demo plan step 4, TRD §6.6).

With the hook on (Supabase dashboard › Authentication › Hooks › Send Email, HTTPS, pointing at
https://api.deleajewelry.com/api/v1/auth/email-hook), Auth sends no email itself: it POSTs each
email here, signed with the hook's secret (Standard Webhooks), and this endpoint decides and
sends. It goes live only after the API is deployed (plan step 7); until then the hosted
project keeps the built-in mailer, which reaches the project's team only. With the hook on, an
API that is down means no Auth emails (invites and password resets fail until it is back).

  1. SEND_EMAIL_HOOK_SECRET empty: 404 (no unauthenticated sender ever exists).
  2. Body over 64 KB, a bad signature, a missing header or a timestamp more than 5 minutes off:
     400/401, nothing read.
  3. public.api_auth_email_decision(user, type, shop in the invite's data) says whether the
     email may go out (invite for an active non-demo shop; reset for a member of an active shop
     and of no demo shop; every other type refused).
  4. Refused: 200 and nothing sent, logged with the type, the user id and the reason. The answer
     looks like any other, so it tells nobody whether an account is a demo account.
  5. Allowed: rendered (app/services/email_templates.py) and sent through the email provider,
     from "DELEA <notifications@...>", with webhook-id as the idempotency key (an Auth retry is
     delivered once). The provider call has 3 s: Auth gives the whole hook 5 s.
     A temporary failure answers 503 (Auth retries on 503); a permanent one 500.

The link token and the email address are never logged and never stored: they exist only in
this request.
"""

import json
import logging
from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from app.api.deps import get_pool
from app.core.config import get_settings
from app.core.standard_webhooks import WebhookRefused, verify
from app.db.session import service_session
from app.services.email_senders import (
    OutgoingEmail,
    PermanentFailure,
    TemporaryFailure,
    build_sender,
    from_address,
)
from app.services.email_templates import Rendered, auth_invite, auth_recovery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_BODY_BYTES = 64 * 1024
PROVIDER_TIMEOUT_SECONDS = 3.0
SENDER_NAME = "DELEA"
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]


def _answer(code: int, message: str | None = None) -> JSONResponse:
    """Auth needs a JSON body on every answer, errors included."""
    return JSONResponse({} if message is None else {"error": message}, status_code=code)


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _link(page: str, token_hash: str, kind: str) -> str:
    base = get_settings().admin_app_url.rstrip("/")
    return f"{base}/{page}?token_hash={quote(token_hash, safe='')}&type={kind}"


def render_auth_email(action: str, business_name: str | None, token_hash: str) -> Rendered:
    if action == "invite":
        link = _link("accept-invite", token_hash, "invite")
        return auth_invite(business_name or "your team", link)
    return auth_recovery(_link("reset-password", token_hash, "recovery"))


@router.post("/email-hook", status_code=status.HTTP_200_OK)
async def auth_email_hook(request: Request, pool: Pool) -> JSONResponse:
    settings = get_settings()
    secret = settings.send_email_hook_secret.strip()
    if not secret:
        return _answer(status.HTTP_404_NOT_FOUND, "Not found")

    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        return _answer(status.HTTP_400_BAD_REQUEST, "Request too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        return _answer(status.HTTP_400_BAD_REQUEST, "Request too large")

    try:
        webhook_id = verify(secret, request.headers, body)
    except WebhookRefused as exc:
        logger.warning("Auth email hook refused: %s", exc)
        return _answer(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    except ValueError as exc:  # SEND_EMAIL_HOOK_SECRET isn't the "v1,whsec_..." Supabase shows
        logger.error("Auth email hook misconfigured: %s", exc)
        return _answer(status.HTTP_500_INTERNAL_SERVER_ERROR, "Hook misconfigured")

    try:
        payload = json.loads(body)
        user = payload["user"]
        email_data = payload["email_data"]
        action = str(email_data.get("email_action_type") or "")
        recipient = str(user.get("email") or "")
        token_hash = str(email_data.get("token_hash") or "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return _answer(status.HTTP_400_BAD_REQUEST, "Invalid payload")

    user_id = _uuid(user.get("id"))
    metadata = user.get("user_metadata") if isinstance(user.get("user_metadata"), dict) else {}
    tenant_id = _uuid(metadata.get("tenant_id"))

    async with service_session(pool) as conn:
        cur = await conn.execute(
            "select decision, business_name from public.api_auth_email_decision(%s, %s, %s)",
            (user_id, action, tenant_id),
        )
        decision, business_name = await cur.fetchone()

    if decision == "send" and (not recipient or not token_hash):
        decision = "incomplete request"
    if decision != "send":
        logger.info(
            "Auth email not sent: type=%s user=%s reason=%s", action[:40], user_id, decision
        )
        return _answer(status.HTTP_200_OK)

    sender = build_sender(settings, timeout=PROVIDER_TIMEOUT_SECONDS)
    if sender is None:
        logger.error("Auth email not sent: email is off (EMAIL_PROVIDER)")
        return _answer(status.HTTP_503_SERVICE_UNAVAILABLE, "Email is off")

    rendered = render_auth_email(action, business_name, token_hash)
    email = OutgoingEmail(
        id=f"auth-{webhook_id}",
        sender=from_address(SENDER_NAME, settings),
        recipient=recipient,
        reply_to=None,
        subject=rendered.subject,
        html=rendered.html,
        text=rendered.text,
    )
    try:
        await sender.send(email)
    except TemporaryFailure as exc:
        logger.warning("Auth email to user %s not sent yet: %s", user_id, exc)
        return _answer(status.HTTP_503_SERVICE_UNAVAILABLE, "Email provider unavailable")
    except PermanentFailure as exc:
        logger.warning("Auth email to user %s refused by the provider: %s", user_id, exc)
        return _answer(status.HTTP_500_INTERNAL_SERVER_ERROR, "Email refused")
    finally:
        await sender.aclose()
    logger.info("Auth email sent: type=%s user=%s", action, user_id)
    return _answer(status.HTTP_200_OK)
