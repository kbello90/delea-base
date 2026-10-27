"""Stripe webhook (Slice B2, TRD §6.14).

POST /stripe/webhook takes events from the shops' connected accounts (Connect). The raw body
(at most 1 MB) is checked against STRIPE_WEBHOOK_SECRET with Stripe's signature scheme and a
5-minute tolerance; anything else is 400 before the body is read as JSON. Each event is then
applied by one function, as payments_writer, in one transaction:

  checkout.session.completed / .expired    stripe_apply_session (completion settles the
                                           invoice in the same transaction)
  refund.created / .updated / .failed      stripe_apply_refund
  account.updated                          stripe_apply_account
  account.application.deauthorized         stripe_apply_account (card off)
  anything else                            stripe_record_event (recorded, ignored)

Each function records the event id first (ON CONFLICT DO NOTHING) in that same transaction,
so a replay changes nothing and a failure rolls the event back for Stripe to retry (any
non-2xx answer). An event that matches no shop, payment or amount is recorded as no_match
and answered 200: retrying it would never help. Live-mode events are refused (test mode only).
"""

import json
import logging
from typing import Annotated, Any
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.api.deps import get_pool
from app.core.config import get_settings
from app.db.session import payments_session
from app.services.stripe_service import verify_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe", tags=["stripe_webhooks"])

MAX_BODY_BYTES = 1_000_000
Pool = Annotated[AsyncConnectionPool, Depends(get_pool)]

SESSION_EVENTS = frozenset({"checkout.session.completed", "checkout.session.expired"})
REFUND_EVENTS = frozenset({"refund.created", "refund.updated", "refund.failed"})


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _id(value: Any) -> str | None:
    """A Stripe reference that may be expanded into an object."""
    if isinstance(value, dict):
        value = value.get("id")
    return value if isinstance(value, str) else None


async def _apply(conn: AsyncConnection, event: dict[str, Any]) -> str:
    event_id, kind = event["id"], event["type"]
    obj = event["data"]["object"]
    account = event.get("account")
    live = bool(event.get("livemode"))
    if kind in SESSION_EVENTS:
        metadata = obj.get("metadata") or {}
        cur = await conn.execute(
            "select public.stripe_apply_session(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event_id,
                kind,
                account,
                live,
                _uuid(metadata.get("tenant_id")),
                _uuid(metadata.get("payment_id")),
                _id(obj.get("id")),
                _id(obj.get("payment_intent")),
                obj.get("payment_status"),
                obj.get("amount_total"),
                obj.get("currency"),
            ),
        )
    elif kind in REFUND_EVENTS:
        cur = await conn.execute(
            "select public.stripe_apply_refund(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event_id,
                kind,
                account,
                live,
                _id(obj.get("id")),
                _id(obj.get("payment_intent")),
                obj.get("amount"),
                obj.get("currency"),
                obj.get("status"),
            ),
        )
    elif kind == "account.updated":
        cur = await conn.execute(
            "select public.stripe_apply_account(%s, %s, %s, %s, %s, %s, false)",
            (
                event_id,
                kind,
                account or _id(obj.get("id")),
                live,
                obj.get("charges_enabled"),
                obj.get("details_submitted"),
            ),
        )
    elif kind == "account.application.deauthorized":
        cur = await conn.execute(
            "select public.stripe_apply_account(%s, %s, %s, %s, null, null, true)",
            (event_id, kind, account, live),
        )
    else:
        cur = await conn.execute(
            "select public.stripe_record_event(%s, %s, %s, %s)", (event_id, kind, account, live)
        )
    (outcome,) = await cur.fetchone()
    return outcome


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def stripe_webhook(request: Request, pool: Pool) -> dict[str, bool]:
    secret = get_settings().stripe_webhook_secret.strip()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Webhooks aren't set up.")
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Too large")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Too large")
    try:
        verify_webhook(body, request.headers.get("stripe-signature"), secret)
    except stripe.SignatureVerificationError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid signature") from None
    try:
        event = json.loads(body)
        event_id, kind = event["id"], event["type"]
        event["data"]["object"]  # noqa: B018 - present on every event
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not a Stripe event") from None

    async with payments_session(pool) as conn:
        outcome = await _apply(conn, event)
    logger.info("Stripe event %s (%s): %s", event_id, kind, outcome)
    return {"received": True}
