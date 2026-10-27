"""Email with the database and the worker (Slice D1): owner alerts from events, the daily
balance digest, one email per source (replays), two workers at once, provider down, a crash
mid-send, demo shops, the reply-to confirmation, the test send, and the provider webhook.

Shops are throwaway tenants. Most tests send through a recording sender; one goes through the
local mail catcher (Mailpit, from the Supabase stack) to prove the real path. Nothing reaches
the internet."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import Settings, get_settings
from app.core.storefront_hosts import host_cache
from app.db.session import close_pool, create_pool, email_session, open_pool
from app.services.email_senders import (
    MailpitSender,
    OutgoingEmail,
    PermanentFailure,
    TemporaryFailure,
)
from app.worker import Worker

from .support import MAILPIT_URL, ThrowawayTenant
from .test_public_checkout import open_shop

pytestmark = pytest.mark.integration

SETTINGS_URL = "/api/v1/settings/emails"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"local-email-webhook-key-for-tests").decode()


class Recorder:
    """A sender that records instead of sending; `fail` makes the next sends fail."""

    name = "recorder"

    def __init__(self) -> None:
        self.sent: list[OutgoingEmail] = []
        self.fail: list[Exception] = []

    async def send(self, email: OutgoingEmail) -> str:
        if self.fail:
            raise self.fail.pop(0)
        self.sent.append(email)
        return f"rec_{len(self.sent)}"


@pytest.fixture
async def pool(settings: Settings) -> AsyncIterator:
    p = create_pool(settings)
    await open_pool(p)
    yield p
    await close_pool(p)


@pytest.fixture(autouse=True)
def _quiet_queue(db: psycopg.Connection, monkeypatch) -> Iterator[None]:
    """Other tests' events and emails would be swept up by the worker: close them first, so
    each test sees only its own."""
    db.execute(
        "update public.automation_events set processed_at = now() where processed_at is null"
    )
    db.execute(
        "update public.email_messages set status = 'suppressed' "
        "where status in ('queued', 'sending')"
    )
    db.execute("delete from public.public_rate_hits")
    host_cache.clear()
    rate_limit.public_reads.reset()
    rate_limit.reply_to_requests.reset()
    monkeypatch.setattr(get_settings(), "resend_webhook_secret", WEBHOOK_SECRET)
    yield


@pytest.fixture
def shop(make_tenant, db: psycopg.Connection) -> dict:
    tenant: ThrowawayTenant = make_tenant(roles=("owner", "staff", "cpa"))
    domain = f"it-{uuid.uuid4().hex[:8]}.localhost:5174"
    open_shop(db, tenant.id, domain)
    ring = db.execute(
        "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty, "
        "low_stock_threshold, is_published) values (%s, %s, 'Halo Ring', 'gold_14k', 14500, 5, "
        "3, true) returning id",
        (tenant.id, f"IT-{uuid.uuid4().hex[:10]}"),
    ).fetchone()[0]
    return {
        "tenant": tenant,
        "origin": f"http://{domain}",
        "ring": str(ring),
        "owner": tenant.emails["owner"].lower(),
    }


def place(client: TestClient, shop: dict) -> dict:
    body = {
        "checkout_key": str(uuid.uuid4()),
        "items": [{"product_id": shop["ring"], "qty": 1}],
        "fulfillment": "ship",
        "payment_choice": "bank_transfer",
        "expected_total_cents": 14500 + 600 + 1160,
        "contact": {
            "first_name": "IT-Sarah",
            "last_name": "Jenkins",
            "email": "sarah@example.com",
            "phone": "(555) 010-0142",
        },
        "ship_to": {
            "line1": "1 Main St",
            "city": "Portland",
            "state": "ME",
            "postal_code": "04101",
        },
        "elapsed_ms": 9000,
    }
    response = client.post("/api/v1/public/orders", json=body, headers={"Origin": shop["origin"]})
    assert response.status_code == 201, response.text
    return response.json()


def messages(db, tenant_id: str) -> list[tuple]:
    return db.execute(
        "select template, recipient, status, kind, attempts from public.email_messages "
        "where tenant_id = %s order by created_at, template",
        (tenant_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Owner alerts
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_a_website_order_alerts_the_owner_once(client, shop, db, pool) -> None:
    order = place(client, shop)
    recorder = Recorder()
    worker = Worker(pool, recorder, get_settings())
    await worker.run_once()
    mine = [e for e in recorder.sent if e.recipient == shop["owner"]]
    assert len(mine) == 1
    alert = mine[0]
    assert alert.subject == f"New website order {order['order_ref']}"
    assert alert.sender.endswith("<notifications@mail.deleajewelry.com>")
    assert "IT-Sarah Jenkins" in alert.text and "$162.60" in alert.text
    # No street address or phone in an email.
    assert "Main St" not in alert.html + alert.text and "010-0142" not in alert.html + alert.text
    # A replayed event queues nothing new.
    db.execute(
        "update public.automation_events set processed_at = null where tenant_id = %s",
        (shop["tenant"].id,),
    )
    await worker.run_once()
    assert len([e for e in recorder.sent if e.recipient == shop["owner"]]) == 1
    assert [m[:3] for m in messages(db, shop["tenant"].id)] == [
        ("new_order", shop["owner"], "sent")
    ]


@pytest.mark.anyio
async def test_low_stock_alerts_once_a_day_and_respects_the_switch(client, shop, db, pool) -> None:
    recorder = Recorder()
    worker = Worker(pool, recorder, get_settings())
    for qty in (2, 5, 1):  # crosses the threshold twice
        db.execute("update public.products set stock_qty = %s where id = %s", (qty, shop["ring"]))
    await worker.run_once()
    assert [m[:2] for m in messages(db, shop["tenant"].id)] == [("low_stock", shop["owner"])]

    owner = shop["tenant"].headers("owner")
    off = client.patch(
        SETTINGS_URL,
        json={"alerts": {"new_order": True, "low_stock": False, "balance_due": True}},
        headers=owner,
    )
    assert off.status_code == 200 and off.json()["alerts"]["low_stock"] is False
    db.execute("delete from public.email_messages where tenant_id = %s", (shop["tenant"].id,))
    db.execute("update public.products set stock_qty = 5 where id = %s", (shop["ring"],))
    db.execute("update public.products set stock_qty = 0 where id = %s", (shop["ring"],))
    await worker.run_once()
    assert messages(db, shop["tenant"].id) == []


@pytest.mark.anyio
async def test_the_balance_due_digest(client, shop, db, pool) -> None:
    order = place(client, shop)
    invoice_id = db.execute(
        "select id from public.invoices where public_token = %s", (order["public_token"],)
    ).fetchone()[0]
    db.execute(
        "insert into public.payments (tenant_id, invoice_id, kind, method, amount_cents, currency) "
        "values (%s, %s, 'payment', 'bank_transfer', 5000, 'USD')",
        (shop["tenant"].id, invoice_id),
    )
    db.execute(
        "update public.invoices set due_date = current_date - 3 where id = %s", (invoice_id,)
    )
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()
    digest = [e for e in recorder.sent if e.subject.startswith("Balances due")]
    assert len(digest) == 1 and digest[0].recipient == shop["owner"]
    assert order["order_ref"] in digest[0].text and "$112.60" in digest[0].text
    # Another pass the same day: no second digest.
    await Worker(pool, recorder, get_settings()).run_once()
    assert len([e for e in recorder.sent if e.subject.startswith("Balances due")]) == 1


# ---------------------------------------------------------------------------
# Sending: two workers, provider down, a crash mid-send
# ---------------------------------------------------------------------------
async def _queue(pool, tenant_id: str, to: str, n: int) -> None:
    async with email_session(pool) as conn:
        for i in range(n):
            await conn.execute(
                "select public.email_enqueue(%s, 'test', 'test', %s, %s, 'Hi', '<p>Hi</p>', 'Hi')",
                (tenant_id, f"it:{uuid.uuid4()}:{i}", to),
            )


@pytest.mark.anyio
async def test_two_workers_never_send_the_same_email(shop, db, pool) -> None:
    await _queue(pool, shop["tenant"].id, shop["owner"], 8)
    first, second = Recorder(), Recorder()
    await asyncio.gather(
        Worker(pool, first, get_settings()).run_once(),
        Worker(pool, second, get_settings()).run_once(),
    )
    ids = [e.id for e in first.sent + second.sent]
    assert len(ids) == 8 and len(set(ids)) == 8
    assert {m[2] for m in messages(db, shop["tenant"].id)} == {"sent"}


@pytest.mark.anyio
async def test_provider_down_then_back_loses_nothing(shop, db, pool) -> None:
    await _queue(pool, shop["tenant"].id, shop["owner"], 1)
    recorder = Recorder()
    recorder.fail.append(TemporaryFailure("Resend answered 503"))
    worker = Worker(pool, recorder, get_settings())
    await worker.run_once()
    row = db.execute(
        "select status, attempts, last_error, next_attempt_at > now() from public.email_messages "
        "where tenant_id = %s",
        (shop["tenant"].id,),
    ).fetchone()
    assert row == ("queued", 1, "Resend answered 503", True)
    db.execute(
        "update public.email_messages set next_attempt_at = now() where tenant_id = %s",
        (shop["tenant"].id,),
    )
    await worker.run_once()
    assert [m[2:5] for m in messages(db, shop["tenant"].id)] == [("sent", "test", 2)]


@pytest.mark.anyio
async def test_a_permanent_refusal_is_not_retried(shop, db, pool) -> None:
    await _queue(pool, shop["tenant"].id, shop["owner"], 1)
    recorder = Recorder()
    recorder.fail.append(PermanentFailure("Resend refused it (422)"))
    worker = Worker(pool, recorder, get_settings())
    await worker.run_once()
    await worker.run_once()
    assert [m[2] for m in messages(db, shop["tenant"].id)] == ["failed"]
    assert recorder.sent == []


@pytest.mark.anyio
async def test_a_crash_mid_send_retries_with_the_same_idempotency_key(shop, db, pool) -> None:
    """The worker died after the provider accepted the email but before recording it: after
    10 minutes it is sent again with the same key (the row id), which Resend deduplicates."""
    await _queue(pool, shop["tenant"].id, shop["owner"], 1)
    message_id = db.execute(
        "select id from public.email_messages where tenant_id = %s", (shop["tenant"].id,)
    ).fetchone()[0]
    db.execute(
        "update public.email_messages set status = 'sending', attempts = 1, "
        "next_attempt_at = now() - interval '11 minutes' where id = %s",
        (message_id,),
    )
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()
    assert [e.id for e in recorder.sent] == [str(message_id)]


# ---------------------------------------------------------------------------
# Demo shops: their own owners only
# ---------------------------------------------------------------------------
@pytest.fixture
def demo(make_tenant) -> ThrowawayTenant:
    return make_tenant(is_demo=True)


@pytest.mark.anyio
async def test_a_demo_shop_emails_its_own_owner_only(
    demo: ThrowawayTenant, client, db, pool
) -> None:
    owner = demo.emails["owner"].lower()
    async with email_session(pool) as conn:
        for kind, to in (
            ("test", owner),
            ("customer", "shopper@example.com"),
            ("reply_to_confirmation", "someone@example.com"),
        ):
            await conn.execute(
                "select public.email_enqueue(%s, %s, 'x', %s, %s, 'Hi', '<p>Hi</p>', 'Hi')",
                (demo.id, kind, f"it:{uuid.uuid4()}", to),
            )
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()
    assert [e.recipient for e in recorder.sent] == [owner]
    statuses = {m[1]: m[2] for m in messages(db, demo.id)}
    assert statuses == {
        owner: "sent",
        "shopper@example.com": "suppressed",
        "someone@example.com": "suppressed",
    }
    # The owner's test from the admin goes to the owner: allowed in a demo shop too.
    test = client.post(f"{SETTINGS_URL}/test", headers=demo.headers("owner"))
    assert test.status_code == 200 and test.json() == {"sent_to": owner, "status": "queued"}


# ---------------------------------------------------------------------------
# The admin: settings, reply-to confirmation, test, through the local mail catcher
# ---------------------------------------------------------------------------
def _mailpit_link(to: str, subject_part: str) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = httpx.get(
            f"{MAILPIT_URL}/api/v1/search", params={"query": f"to:{to}"}, timeout=10
        ).json()
        for message in found["messages"]:
            if subject_part in message["Subject"]:
                body = httpx.get(f"{MAILPIT_URL}/api/v1/message/{message['ID']}", timeout=10).json()
                return body["Text"]
        time.sleep(0.3)
    raise AssertionError(f"no '{subject_part}' email to {to}")


@pytest.mark.anyio
async def test_reply_to_is_confirmed_by_its_link_through_the_mail_catcher(
    client, shop, db, pool, settings
) -> None:
    owner = shop["tenant"].headers("owner")
    for role in ("staff", "cpa"):
        assert client.get(SETTINGS_URL, headers=shop["tenant"].headers(role)).status_code == 403
    address = f"it-replies-{uuid.uuid4().hex[:6]}@example.com"
    pending = client.post(f"{SETTINGS_URL}/reply-to", json={"email": address}, headers=owner)
    assert pending.status_code == 200, pending.text
    assert (pending.json()["reply_to"], pending.json()["reply_to_pending"]) == (None, address)

    await Worker(pool, MailpitSender(settings.mailpit_url), get_settings()).run_once()
    text = _mailpit_link(address, "Confirm replies for")
    link = next(word for word in text.split() if "/confirm-reply-to?token=" in word)
    token = parse_qs(urlsplit(link).query)["token"][0]

    confirmed = client.post("/api/v1/email/confirm-reply-to", json={"token": token})
    assert confirmed.status_code == 200, confirmed.text
    again = client.post("/api/v1/email/confirm-reply-to", json={"token": token})
    assert again.status_code == 400
    state = client.get(SETTINGS_URL, headers=owner).json()
    assert (state["reply_to"], state["reply_to_pending"]) == (address, None)

    # A test goes to the owner's own sign-in email, with the confirmed reply-to.
    test = client.post(f"{SETTINGS_URL}/test", headers=owner)
    assert test.status_code == 200 and test.json()["sent_to"] == shop["owner"]
    assert (
        client.post(f"{SETTINGS_URL}/test", headers=shop["tenant"].headers("staff")).status_code
        == 403
    )
    await Worker(pool, MailpitSender(settings.mailpit_url), get_settings()).run_once()
    found = httpx.get(
        f"{MAILPIT_URL}/api/v1/search", params={"query": f"to:{shop['owner']}"}, timeout=10
    ).json()
    tests = [m for m in found["messages"] if m["Subject"].startswith("Test email from")]
    assert tests and tests[0]["ReplyTo"][0]["Address"] == address


def test_sender_name_and_validation(client, shop) -> None:
    owner = shop["tenant"].headers("owner")
    named = client.patch(SETTINGS_URL, json={"sender_name": "Aurora Studio"}, headers=owner).json()
    assert named["from_address"] == "Aurora Studio <notifications@mail.deleajewelry.com>"
    for bad in ({"sender_name": "x<y>"}, {"sender_name": "a\nb"}, {"provider": "resend"}):
        assert client.patch(SETTINGS_URL, json=bad, headers=owner).status_code == 422
    assert (
        client.post(
            f"{SETTINGS_URL}/reply-to", json={"email": "not-an-email"}, headers=owner
        ).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# The provider webhook
# ---------------------------------------------------------------------------
def _signed(payload: dict, msg_id: str, stamp: int | None = None) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    stamp = stamp or int(time.time())
    key = base64.b64decode(WEBHOOK_SECRET.removeprefix("whsec_"))
    sig = base64.b64encode(
        hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return body, {
        "svix-id": msg_id,
        "svix-timestamp": str(stamp),
        "svix-signature": f"v1,{sig}",
        "Content-Type": "application/json",
    }


@pytest.mark.anyio
async def test_a_bounce_suppresses_the_address(client, shop, db, pool) -> None:
    address = f"it-bounce-{uuid.uuid4().hex[:6]}@example.com"
    msg_id = f"msg_it_{uuid.uuid4().hex[:10]}"
    body, headers = _signed({"type": "email.bounced", "data": {"to": [address]}}, msg_id)
    assert (
        client.post("/api/v1/email/webhook", content=body + b" ", headers=headers).status_code
        == 400
    )
    assert client.post("/api/v1/email/webhook", content=body, headers=headers).status_code == 200
    assert client.post("/api/v1/email/webhook", content=body, headers=headers).status_code == 200
    assert db.execute(
        "select reason from public.email_suppressions where email = %s", (address,)
    ).fetchone() == ("bounced",)
    async with email_session(pool) as conn:
        cur = await conn.execute(
            "select public.email_enqueue(%s, 'customer', 'x', %s, %s, 'Hi', '<p>Hi</p>', 'Hi')",
            (shop["tenant"].id, f"it:{uuid.uuid4()}", address),
        )
        (message_id,) = await cur.fetchone()
    assert db.execute(
        "select status from public.email_messages where id = %s", (message_id,)
    ).fetchone() == ("suppressed",)
    db.execute("delete from public.email_suppressions where email = %s", (address,))
    db.execute("delete from public.email_provider_events where event_id = %s", (msg_id,))
