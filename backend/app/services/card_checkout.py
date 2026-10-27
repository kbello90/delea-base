"""Starting a card payment for a storefront order, and closing open ones (Slice B2, TRD §6.14).

A card payment is a pending payments row for the order's balance, written by
storefront_card_payment_start (as storefront_writer), plus a Stripe Checkout Session on the
shop's own account. The session is created with the idempotency key
`delea:checkout:<payment id>`, so a retried call never makes a second session; its id, link and
expiry are then attached to the row. If Stripe can't be reached the attempt is marked failed:
the order stays, and the shopper's tracking page offers Pay now.

The webhook (app/api/v1/stripe_webhooks.py) records what Stripe says happened.
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.db.session import checkout_session, payments_session, service_session
from app.services.stripe_service import CheckoutRequest, StripeGateway, StripeUnavailable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CardStart:
    """`checkout_url` when a session is ready; otherwise `refused` says why: not_found,
    closed, card_off, paid, too_small or stripe (Stripe couldn't be reached)."""

    checkout_url: str | None = None
    refused: str | None = None


async def start_card_payment(
    pool: AsyncConnectionPool,
    gateway: StripeGateway | None,
    tenant_id: str,
    token: UUID,
    origin: str,
) -> CardStart:
    if gateway is None:
        return CardStart(refused="card_off")
    async with checkout_session(pool) as conn:
        cur = await conn.execute(
            "select public.storefront_card_payment_start(%s, %s)", (tenant_id, token)
        )
        (started,) = await cur.fetchone()
    if started["status"] == "reuse":
        return CardStart(checkout_url=started["checkout_url"])
    if started["status"] != "start":
        return CardStart(refused=started["status"])

    account_id = started["account_id"]
    # Older open sessions (another tab, a different balance) can't be paid any more.
    for session_id in started["open_sessions"]:
        try:
            await gateway.expire_checkout_session(account_id, session_id)
        except StripeUnavailable:
            continue  # it expires on its own within the hour; the webhook records that
        async with payments_session(pool) as writer:
            await writer.execute(
                "select public.stripe_close_session(%s, %s)", (tenant_id, session_id)
            )

    payment_id = started["payment_id"]
    tracking = f"{origin.rstrip('/')}/orders/{token}"
    try:
        session = await gateway.create_checkout_session(
            CheckoutRequest(
                account_id=account_id,
                amount_cents=started["amount_cents"],
                currency=started["currency"],
                description=f"Order {started['order_ref']}",
                metadata={
                    "tenant_id": tenant_id,
                    "invoice_id": str(started["invoice_id"]),
                    "payment_id": str(payment_id),
                    "order_ref": started["order_ref"],
                },
                success_url=f"{tracking}?paid=1",
                cancel_url=tracking,
                idempotency_key=f"delea:checkout:{payment_id}",
            )
        )
    except StripeUnavailable:
        async with checkout_session(pool) as conn:
            await conn.execute(
                "select public.storefront_card_payment_failed(%s, %s)", (tenant_id, payment_id)
            )
        return CardStart(refused="stripe")
    async with checkout_session(pool) as conn:
        await conn.execute(
            "select public.storefront_card_payment_attach(%s, %s, %s, %s, %s)",
            (tenant_id, payment_id, session.id, session.url, session.expires_at),
        )
    return CardStart(checkout_url=session.url)


class CardSessionOpen(Exception):
    """An open card session couldn't be closed: a manual payment now could be paid twice."""


async def close_open_card_sessions(
    pool: AsyncConnectionPool,
    gateway: StripeGateway | None,
    tenant_id: str,
    invoice_id: UUID,
) -> None:
    """Before a payment is recorded by hand: expire the order's open Checkout Sessions at
    Stripe, so the shopper can't also pay by card. Raises CardSessionOpen if one can't be."""
    async with service_session(pool) as conn:
        cur = await conn.execute(
            "select p.external_ref, a.external_account_id from public.payments p "
            "join public.payment_accounts a on a.tenant_id = p.tenant_id and a.provider = 'stripe' "
            "where p.tenant_id = %s and p.invoice_id = %s and p.provider = 'stripe' "
            "and p.status = 'pending' and p.external_ref is not null",
            (tenant_id, invoice_id),
        )
        open_sessions = await cur.fetchall()
    for session_id, account_id in open_sessions:
        if gateway is None:
            raise CardSessionOpen(session_id)
        try:
            await gateway.expire_checkout_session(account_id, session_id)
        except StripeUnavailable as exc:
            raise CardSessionOpen(session_id) from exc
        async with payments_session(pool) as writer:
            await writer.execute(
                "select public.stripe_close_session(%s, %s)", (tenant_id, session_id)
            )
