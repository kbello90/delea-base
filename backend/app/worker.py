"""The email worker (TRD §6.16): `python -m app.worker` (`--once` for a single pass).

Each pass, all as `email_worker` (no table grants; the outbox functions only):
  1. Events: claim unprocessed automation events (FOR UPDATE SKIP LOCKED: two workers never
     take the same one), write the owner alerts and customer emails they call for into the
     outbox, and mark them processed, in one transaction. Each outbox row is unique per shop
     and source key (`event:<id>:<template>:<recipient>`, per product and day for low stock,
     `customer:<template>:<order>` for customer emails, per payment for a deposit), so a
     replay queues nothing new. Events older than two days are closed without email.
     Customer emails (D2) go to website orders only: order received, payment received,
     deposit received, in production (once per order) and shipped, each switchable per shop.
  2. Balances due: once an hour, a daily digest per shop (one per owner and day).
  3. Sending: claim due emails (marked 'sending' and committed, so no other worker takes
     them), send each with its row id as the idempotency key, and record sent, retry later
     (1 min, 5 min, 30 min, 2 h, 6 h) or failed. Provider down: emails wait; nothing is lost.

What may be emailed to whom (owners only for alerts; demo shops: their own owners only;
suppressed addresses never; daily limits) is enforced by the database, not here.
"""

import argparse
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import Settings, get_settings
from app.core.storefront_hosts import storefront_url
from app.db.session import close_pool, create_pool, email_session, open_pool
from app.services import email_templates as templates
from app.services.email_senders import (
    EmailSender,
    OutgoingEmail,
    PermanentFailure,
    TemporaryFailure,
    build_sender,
    check_email_settings,
    from_address,
)

logger = logging.getLogger("delea.worker")

RETRY_DELAYS = (60, 300, 1800, 7200, 21600)  # seconds after attempts 1..5
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
EVENT_BATCH = 100
SEND_BATCH = 50
SEND_INTERVAL = 0.12  # under Resend's 10 requests a second
BALANCE_DUE_EVERY = timedelta(hours=1)


@dataclass
class PassReport:
    events: int = 0
    queued: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


async def _enqueue(
    conn: AsyncConnection,
    tenant_id: Any,
    kind: str,
    template: str,
    key: str,
    to: str,
    content: templates.Rendered,
) -> bool:
    cur = await conn.execute(
        "select public.email_enqueue(%s, %s, %s, %s, %s, %s, %s, %s)",
        (tenant_id, kind, template, key, to, content.subject, content.html, content.text),
    )
    (queued,) = await cur.fetchone()
    return queued is not None


def customer_templates(event_type: str, payload: dict) -> list[tuple[str, str]]:
    """The customer emails an event calls for, each with its source key: one per order and
    email (one per payment for a deposit), so a replay or a second route to the same email
    (production started, then the status moving to in production) queues nothing new."""
    invoice = payload.get("invoice_id")
    if not invoice:
        return []
    out: list[tuple[str, str]] = []
    if event_type == "order_placed":
        out.append(("order_received", f"customer:order_received:{invoice}"))
    elif event_type == "deposit_received" and payload.get("payment_id"):
        out.append(("deposit_received", f"customer:deposit_received:{payload['payment_id']}"))
    elif event_type == "production_started":
        out.append(("in_production", f"customer:in_production:{invoice}"))
    elif event_type == "order_status_changed":
        # Leaving quote is the payment (in full: a deposit is still a quote).
        if payload.get("from_status") == "quote":
            out.append(("payment_received", f"customer:payment_received:{invoice}"))
        if payload.get("to_status") == "in_production":
            out.append(("in_production", f"customer:in_production:{invoice}"))
        elif payload.get("to_status") == "shipped":
            out.append(("shipped", f"customer:shipped:{invoice}"))
    return out


async def customer_emails(
    conn: AsyncConnection, settings: Settings, event: dict, report: PassReport
) -> None:
    """Website orders only (email_customer_context is null for any other invoice). What the
    shopper may receive, and whether at all (demo shops, suppressed addresses, daily limits),
    is still decided by email_enqueue."""
    wanted = customer_templates(event["event_type"], event["payload"])
    if not wanted:
        return
    cur = await conn.execute(
        "select public.email_customer_context(%s, %s)",
        (event["tenant_id"], event["payload"]["invoice_id"]),
    )
    (ctx,) = await cur.fetchone()
    if not ctx or ctx["cancelled"] or not ctx["storefront_domain"]:
        return
    shop = event["context"]
    website = storefront_url(ctx["storefront_domain"], settings.is_dev_environment)
    payload = event["payload"]
    for template, key in wanted:
        if not ctx["switches"].get(template, True):
            continue
        facts = ctx
        if template == "deposit_received" and "balance_cents" in payload:
            # As at the deposit, even if the rest has been paid since.
            facts = {
                **ctx,
                "amount_paid_cents": payload["paid_cents"],
                "balance_cents": payload["balance_cents"],
            }
        order = templates.order_from_context(
            shop["business_name"],
            shop.get("primary_color"),
            facts,
            f"{website}/orders/{ctx['public_token']}",
            website,
            ctx["intros"].get(template),
        )
        content = templates.customer_email(template, order, payload.get("amount_cents"))
        if await _enqueue(conn, event["tenant_id"], "customer", template, key, ctx["to"], content):
            report.queued += 1


async def expand_events(pool: AsyncConnectionPool, settings: Settings, report: PassReport) -> None:
    admin = settings.admin_app_url.rstrip("/")
    today = datetime.now(UTC).date().isoformat()
    async with email_session(pool) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("select * from public.email_claim_events(%s)", (EVENT_BATCH,))
            events = await cur.fetchall()
        for event in events:
            ctx, payload = event["context"], event["payload"]
            shop, color = ctx["business_name"], ctx.get("primary_color")
            invoice, product = ctx.get("invoice"), ctx.get("product")
            for owner in ctx["owners"]:
                content, template, key = None, "", ""
                if (
                    event["event_type"] == "order_placed"
                    and invoice
                    and invoice["source"] == "storefront"
                    and ctx["alerts"]["new_order"]
                ):
                    template, key = "new_order", f"event:{event['event_id']}:new_order:{owner}"
                    content = templates.new_order_alert(
                        shop, color, invoice, f"{admin}/invoices/{payload['invoice_id']}"
                    )
                elif event["event_type"] == "stock_low" and product and ctx["alerts"]["low_stock"]:
                    # At most once per product and day, however often stock crosses the line.
                    template, key = (
                        "low_stock",
                        f"low_stock:{payload['product_id']}:{today}:{owner}",
                    )
                    content = templates.low_stock_alert(
                        shop, color, product, f"{admin}/products?low_stock=true"
                    )
                if content is not None and await _enqueue(
                    conn, event["tenant_id"], "owner_alert", template, key, owner, content
                ):
                    report.queued += 1
            await customer_emails(conn, settings, event, report)
        if events:
            await conn.execute(
                "select public.email_mark_events_processed(%s)", ([e["event_id"] for e in events],)
            )
        report.events += len(events)


async def balance_due(pool: AsyncConnectionPool, settings: Settings, report: PassReport) -> None:
    admin = settings.admin_app_url.rstrip("/")
    today = datetime.now(UTC).date()
    async with email_session(pool) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("select * from public.email_balance_due()")
            shops = await cur.fetchall()
        for shop in shops:
            content = templates.balance_due_digest(
                shop["business_name"],
                None,
                shop["currency"],
                shop["invoices"],
                today,
                f"{admin}/invoices",
            )
            for owner in shop["owners"]:
                key = f"balance_due:{today.isoformat()}:{owner}"
                if await _enqueue(
                    conn, shop["tenant_id"], "owner_alert", "balance_due", key, owner, content
                ):
                    report.queued += 1


def retry_at(attempts: int, now: datetime) -> datetime | None:
    """When to try again after `attempts` tries; None: give up."""
    if attempts >= MAX_ATTEMPTS:
        return None
    return now + timedelta(seconds=RETRY_DELAYS[attempts - 1])


async def send_due(
    pool: AsyncConnectionPool, sender: EmailSender, settings: Settings, report: PassReport
) -> None:
    async with email_session(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from public.email_claim_messages(%s)", (SEND_BATCH,))
        claimed = await cur.fetchall()
    for n, message in enumerate(claimed):
        if n:
            await asyncio.sleep(SEND_INTERVAL)
        email = OutgoingEmail(
            id=str(message["id"]),
            sender=from_address(message["from_name"], settings),
            recipient=message["recipient"],
            reply_to=message["reply_to"],
            subject=message["subject"],
            html=message["html_body"],
            text=message["text_body"],
        )
        try:
            provider_id = await sender.send(email)
        except TemporaryFailure as exc:
            again = retry_at(message["attempts"], datetime.now(UTC))
            async with email_session(pool) as conn:
                await conn.execute(
                    "select public.email_mark_failed(%s, %s, %s)", (message["id"], str(exc), again)
                )
            if again is None:
                report.failed += 1
            else:
                report.retried += 1
            report.errors.append(str(exc))
            continue
        except PermanentFailure as exc:
            async with email_session(pool) as conn:
                await conn.execute(
                    "select public.email_mark_failed(%s, %s, null)", (message["id"], str(exc))
                )
            report.failed += 1
            report.errors.append(str(exc))
            continue
        async with email_session(pool) as conn:
            await conn.execute(
                "select public.email_mark_sent(%s, %s)", (message["id"], provider_id)
            )
        report.sent += 1


class Worker:
    def __init__(self, pool: AsyncConnectionPool, sender: EmailSender | None, settings: Settings):
        self.pool, self.sender, self.settings = pool, sender, settings
        self._balance_due_at = 0.0

    async def run_once(self) -> PassReport:
        report = PassReport()
        await expand_events(self.pool, self.settings, report)
        if time.monotonic() - self._balance_due_at >= BALANCE_DUE_EVERY.total_seconds() or (
            not self._balance_due_at
        ):
            await balance_due(self.pool, self.settings, report)
            self._balance_due_at = time.monotonic()
        if self.sender is not None:  # email off: the outbox fills and waits
            await send_due(self.pool, self.sender, self.settings, report)
        return report


async def main(once: bool) -> None:
    settings = get_settings()
    check_email_settings(settings)
    sender = build_sender(settings)
    logger.info("email worker: %s", sender.name if sender else "email off (queued only)")
    pool = create_pool(settings)
    await open_pool(pool)
    worker = Worker(pool, sender, settings)
    try:
        while True:
            report = await worker.run_once()
            if report.events or report.sent or report.retried or report.failed:
                logger.info(
                    "events %d, queued %d, sent %d, retried %d, failed %d",
                    report.events,
                    report.queued,
                    report.sent,
                    report.retried,
                    report.failed,
                )
            if once:
                return
            await asyncio.sleep(1 if (report.events or report.sent) else 5)
    finally:
        await close_pool(pool)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="DELEA email worker")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args()
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(main(args.once))
