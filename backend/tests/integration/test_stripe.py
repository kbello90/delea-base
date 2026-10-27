"""Stripe in Slice B2 with a fake Stripe client (CI is offline and has no keys): the owner's
payment settings and onboarding, card at checkout, Pay now, the webhook (signed here with
Stripe's own scheme and a local test secret), refunds, accounts, manual payments while a card
session is open, and the startup refusal of live keys.

Every shop is a throwaway tenant (never the seed tenants A and B), deleted afterwards."""

import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api.v1 import stripe_webhooks
from app.core import rate_limit
from app.core.config import Settings, get_settings
from app.core.storefront_hosts import host_cache
from app.main import app
from app.services.stripe_service import (
    FakeStripeGateway,
    StripeSettingsError,
    check_stripe_settings,
    gateway_dependency,
)

from .support import ThrowawayTenant
from .test_public_checkout import open_shop

pytestmark = pytest.mark.integration

INVOICES = "/api/v1/invoices"
PAYMENTS = "/api/v1/settings/payments"
WEBHOOK = "/api/v1/stripe/webhook"
SECRET = "whsec_it_local_only"  # a made-up secret, for these tests only
TOTAL = 14500 + 600 + 1160  # the ring, shipping, 8% tax


@pytest.fixture
def fake() -> Iterator[FakeStripeGateway]:
    gateway = FakeStripeGateway()
    app.dependency_overrides[gateway_dependency] = lambda: gateway
    yield gateway
    app.dependency_overrides.pop(gateway_dependency, None)


@pytest.fixture(autouse=True)
def _fresh(db: psycopg.Connection, monkeypatch) -> Iterator[None]:
    db.execute("delete from public.public_rate_hits")
    host_cache.clear()
    rate_limit.public_reads.reset()
    rate_limit.public_pay.reset()
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", SECRET)
    yield
    host_cache.clear()
    db.execute("delete from public.stripe_events where event_id like 'evt_it_%'")


@pytest.fixture
def shop(make_tenant, db: psycopg.Connection) -> dict:
    """A shop that ships, takes bank transfer, and whose Stripe account can take charges."""
    tenant: ThrowawayTenant = make_tenant(roles=("owner", "staff", "cpa"))
    domain = f"it-{uuid.uuid4().hex[:8]}.localhost:5174"
    open_shop(db, tenant.id, domain)
    ring = db.execute(
        "insert into public.products (tenant_id, sku, name, metal, price_cents, stock_qty, "
        "is_published) values (%s, %s, 'Halo Ring', 'gold_14k', 14500, 3, true) returning id",
        (tenant.id, f"IT-{uuid.uuid4().hex[:10]}"),
    ).fetchone()[0]
    account = "acct_IT" + uuid.uuid4().hex[:12]
    db.execute(
        "insert into public.payment_accounts (tenant_id, external_account_id, charges_enabled, "
        "details_submitted) values (%s, %s, true, true)",
        (tenant.id, account),
    )
    return {
        "tenant": tenant,
        "origin": f"http://{domain}",
        "ring": str(ring),
        "account": account,
    }


def order_body(shop: dict, choice="card", qty=1) -> dict:
    return {
        "checkout_key": str(uuid.uuid4()),
        "items": [{"product_id": shop["ring"], "qty": qty}],
        "fulfillment": "ship",
        "payment_choice": choice,
        "expected_total_cents": 14500 * qty
        + (0 if 14500 * qty >= 15000 else 600)
        + (14500 * qty * 800 + 5000) // 10000,
        "contact": {"first_name": "IT-Lena", "last_name": "Ortiz", "email": "lena@example.com"},
        "ship_to": {"line1": "9 Elm St", "city": "Bangor", "state": "ME", "postal_code": "04401"},
        "elapsed_ms": 9000,
    }


def place(client: TestClient, shop: dict, choice="card", qty=1, code=201, body=None) -> dict:
    body = body or order_body(shop, choice, qty)
    response = client.post("/api/v1/public/orders", json=body, headers={"Origin": shop["origin"]})
    assert response.status_code == code, response.text
    return response.json()


def invoice(db, token: str) -> dict:
    row = db.execute(
        "select id, order_status::text, payment_method::text, refunded_at, "
        "public.invoice_amount_paid(id) from public.invoices where public_token = %s",
        (token,),
    ).fetchone()
    return dict(zip(("id", "status", "method", "refunded_at", "paid"), row, strict=True))


def stock(db, shop: dict) -> int:
    return db.execute(
        "select stock_qty from public.products where id = %s", (shop["ring"],)
    ).fetchone()[0]


def pending(db, invoice_id) -> tuple:
    return db.execute(
        "select id, external_ref, amount_cents, status from public.payments "
        "where invoice_id = %s and provider = 'stripe' order by created_at desc limit 1",
        (invoice_id,),
    ).fetchone()


def signed(payload: dict, secret: str = SECRET, at: int | None = None) -> tuple[bytes, dict]:
    """Stripe's scheme: v1 = HMAC-SHA256(secret, "<t>.<body>")."""
    body = json.dumps(payload).encode()
    t = at or int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return body, {"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"}


def event(kind: str, obj: dict, account: str, *, live=False, event_id=None) -> dict:
    return {
        "id": event_id or f"evt_it_{uuid.uuid4().hex[:16]}",
        "object": "event",
        "type": kind,
        "account": account,
        "livemode": live,
        "api_version": "2026-09-30.endive",
        "data": {"object": obj},
    }


def session_completed(db, shop: dict, token: str, *, amount=None, account=None, **kw) -> dict:
    inv = invoice(db, token)
    payment_id, session_id, cents, _ = pending(db, inv["id"])
    return event(
        "checkout.session.completed",
        {
            "id": session_id,
            "object": "checkout.session",
            "payment_status": "paid",
            "amount_total": cents if amount is None else amount,
            "currency": "usd",
            "payment_intent": f"pi_it_{payment_id.hex}",
            "metadata": {"tenant_id": shop["tenant"].id, "payment_id": str(payment_id)},
        },
        account or shop["account"],
        **kw,
    )


def deliver(client: TestClient, payload: dict, code=200, **sign) -> None:
    body, headers = signed(payload, **sign)
    response = client.post(WEBHOOK, content=body, headers=headers)
    assert response.status_code == code, response.text


# ---------------------------------------------------------------------------
# Startup: live keys refused
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", ["sk_live_abc123", "rk_live_abc123"])
def test_a_live_key_stops_the_api(key: str) -> None:
    with pytest.raises(StripeSettingsError) as refused:
        check_stripe_settings(Settings(stripe_secret_key=key, environment="local"))
    assert key not in str(refused.value)  # the message never repeats the key


def test_test_keys_start_and_the_fake_is_local_only() -> None:
    check_stripe_settings(Settings(stripe_secret_key="sk_test_abc", environment="production"))
    check_stripe_settings(Settings(stripe_gateway="fake", environment="local"))
    with pytest.raises(StripeSettingsError):
        check_stripe_settings(Settings(stripe_gateway="fake", environment="production"))


# ---------------------------------------------------------------------------
# The owner's payment settings and onboarding
# ---------------------------------------------------------------------------
def test_onboarding_creates_one_account_and_the_return_reads_stripe(
    client: TestClient, make_tenant, fake: FakeStripeGateway
) -> None:
    tenant: ThrowawayTenant = make_tenant(roles=("owner", "staff", "cpa"))
    owner = tenant.headers("owner")
    assert client.get(PAYMENTS, headers=owner).json() == {
        "stripe_configured": True,
        "is_demo": False,
        "account": None,
        "card_payments": False,
    }
    for role in ("staff", "cpa"):
        assert client.get(PAYMENTS, headers=tenant.headers(role)).status_code == 403
        link = client.post(f"{PAYMENTS}/stripe/onboarding-link", headers=tenant.headers(role))
        assert link.status_code == 403

    first = client.post(f"{PAYMENTS}/stripe/onboarding-link", headers=owner)
    assert first.status_code == 200 and first.json()["url"].startswith(
        "https://connect.stripe.com/"
    )
    again = client.post(f"{PAYMENTS}/stripe/onboarding-link", headers=owner)
    assert again.status_code == 200
    created = [c for c in fake.calls if c[0] == "create_account"]
    assert len(created) == 1  # the second link reuses the account
    link_call = next(c for c in fake.calls if c[0] == "account_link")
    assert link_call[1]["return_url"].endswith("/settings?stripe=return")

    pending_state = client.get(PAYMENTS, headers=owner).json()
    assert pending_state["account"]["charges_enabled"] is False
    assert pending_state["card_payments"] is False
    fake.complete_onboarding(pending_state["account"]["account_id"])  # the owner finished
    done = client.get(PAYMENTS, headers=owner).json()
    assert (done["account"]["charges_enabled"], done["card_payments"]) == (True, True)


def test_demo_shops_cannot_connect_and_no_stripe_is_503(
    client: TestClient, make_tenant, fake: FakeStripeGateway
) -> None:
    demo: ThrowawayTenant = make_tenant(is_demo=True)
    refused = client.post(f"{PAYMENTS}/stripe/onboarding-link", headers=demo.headers("owner"))
    assert refused.status_code == 403
    assert not fake.calls
    app.dependency_overrides[gateway_dependency] = lambda: None
    tenant: ThrowawayTenant = make_tenant()
    off = client.post(f"{PAYMENTS}/stripe/onboarding-link", headers=tenant.headers("owner"))
    assert off.status_code == 503
    assert (
        client.get(PAYMENTS, headers=tenant.headers("owner")).json()["stripe_configured"] is False
    )


# ---------------------------------------------------------------------------
# Card at checkout
# ---------------------------------------------------------------------------
def test_card_is_offered_only_when_stripe_and_the_account_can_take_it(
    client: TestClient, shop, db, fake: FakeStripeGateway
) -> None:
    store = client.get("/api/v1/public/store", headers={"Origin": shop["origin"]}).json()
    assert store["payment_choices"] == ["bank_transfer", "pay_on_pickup", "card"]
    db.execute(
        "update public.payment_accounts set charges_enabled = false where tenant_id = %s",
        (shop["tenant"].id,),
    )
    store = client.get("/api/v1/public/store", headers={"Origin": shop["origin"]}).json()
    assert store["payment_choices"] == ["bank_transfer", "pay_on_pickup"]
    refused = client.post(
        "/api/v1/public/orders", json=order_body(shop), headers={"Origin": shop["origin"]}
    )
    assert refused.status_code == 422  # the database refuses card for this shop
    db.execute(
        "update public.payment_accounts set charges_enabled = true where tenant_id = %s",
        (shop["tenant"].id,),
    )
    app.dependency_overrides[gateway_dependency] = lambda: None  # this server has no Stripe
    store = client.get("/api/v1/public/store", headers={"Origin": shop["origin"]}).json()
    assert "card" not in store["payment_choices"]
    assert (
        client.post(
            "/api/v1/public/orders", json=order_body(shop), headers={"Origin": shop["origin"]}
        ).status_code
        == 422
    )


def test_a_card_order_gets_checkout_for_its_total_once(
    client: TestClient, shop, db, fake: FakeStripeGateway
) -> None:
    body = order_body(shop)
    placed = place(client, shop, body=body)
    assert placed["checkout_url"].startswith("https://checkout.stripe.com/c/pay/cs_test_fake")
    assert placed["status"] == "awaiting_payment" and placed["payment_choice"] == "card"
    sessions = [c[1]["request"] for c in fake.calls if c[0] == "create_checkout_session"]
    assert len(sessions) == 1
    request = sessions[0]
    inv = invoice(db, placed["public_token"])
    payment_id, session_id, cents, status = pending(db, inv["id"])
    assert (request.account_id, request.amount_cents, request.currency) == (
        shop["account"],
        TOTAL,
        "USD",
    )
    assert request.idempotency_key == f"delea:checkout:{payment_id}"
    assert request.metadata == {
        "tenant_id": shop["tenant"].id,
        "invoice_id": str(inv["id"]),
        "payment_id": str(payment_id),
        "order_ref": placed["order_ref"],
    }
    assert request.success_url == f"{shop['origin']}/orders/{placed['public_token']}?paid=1"
    assert (cents, status) == (TOTAL, "pending") and session_id.startswith("cs_test_fake")

    # A double submit: the same order and the same open session, no second session.
    again = place(client, shop, body=body, code=200)
    assert again["checkout_url"] == placed["checkout_url"]
    assert len([c for c in fake.calls if c[0] == "create_checkout_session"]) == 1
    # Tracking reads never carry a checkout link.
    tracked = client.get(
        f"/api/v1/public/orders/{placed['public_token']}", headers={"Origin": shop["origin"]}
    ).json()
    assert tracked["checkout_url"] is None


def test_stripe_down_at_checkout_keeps_the_order_and_pay_now_retries(
    client: TestClient, shop, db, fake: FakeStripeGateway
) -> None:
    fake.fail.add("create_checkout_session")
    placed = place(client, shop)
    assert placed["checkout_url"] is None  # the tracking page offers Pay now
    inv = invoice(db, placed["public_token"])
    assert pending(db, inv["id"])[3] == "failed"
    paid = client.post(
        f"/api/v1/public/orders/{placed['public_token']}/pay", headers={"Origin": shop["origin"]}
    )
    assert paid.status_code == 200 and paid.json()["checkout_url"].startswith("https://")
    assert paid.headers["cache-control"] == "no-store"
    assert pending(db, inv["id"])[3] == "pending"


def test_pay_now_after_a_deposit_charges_the_balance(
    client: TestClient, shop, db, fake: FakeStripeGateway
) -> None:
    placed = place(client, shop, choice="bank_transfer")
    inv = invoice(db, placed["public_token"])
    recorded = client.post(
        f"{INVOICES}/{inv['id']}/payments",
        json={"amount_cents": 5000, "method": "bank_transfer"},
        headers=shop["tenant"].headers("owner"),
    )
    assert recorded.status_code == 201, recorded.text
    paid = client.post(
        f"/api/v1/public/orders/{placed['public_token']}/pay", headers={"Origin": shop["origin"]}
    )
    assert paid.status_code == 200, paid.text
    request = [c[1]["request"] for c in fake.calls if c[0] == "create_checkout_session"][-1]
    assert request.amount_cents == TOTAL - 5000  # the balance, never the total


def test_pay_now_refusals(client: TestClient, shop, db, fake: FakeStripeGateway) -> None:
    origin = {"Origin": shop["origin"]}
    placed = place(client, shop, choice="bank_transfer")
    url = f"/api/v1/public/orders/{placed['public_token']}/pay"
    inv = invoice(db, placed["public_token"])
    # A bank-transfer order may still be paid by card; a 30-cent balance may not.
    owner = shop["tenant"].headers("owner")
    recorded = client.post(
        f"{INVOICES}/{inv['id']}/payments",
        json={"amount_cents": TOTAL - 30, "method": "bank_transfer"},
        headers=owner,
    )
    assert recorded.status_code == 201, recorded.text
    small = client.post(url, headers=origin)
    assert small.status_code == 409 and "pay it with the shop" in small.json()["detail"]
    client.post(
        f"{INVOICES}/{inv['id']}/payments",
        json={"amount_cents": 30, "method": "cash"},
        headers=owner,
    )
    assert client.post(url, headers=origin).json()["detail"] == "This order is already paid."
    other = client.post(f"/api/v1/public/orders/{uuid.uuid4()}/pay", headers=origin)
    assert other.status_code == 404
    rate_limit.public_pay.reset()
    for _ in range(10):
        client.post(url, headers=origin)
    assert client.post(url, headers=origin).status_code == 429


# ---------------------------------------------------------------------------
# The webhook
# ---------------------------------------------------------------------------
def test_signature_size_and_secret(client: TestClient, shop, db, fake, monkeypatch) -> None:
    payload = event("customer.created", {"id": "cus_1"}, shop["account"])
    deliver(client, payload, code=400, secret="whsec_wrong")
    deliver(client, payload, code=400, at=int(time.time()) - 400)  # older than 5 minutes
    body, headers = signed(payload)
    assert client.post(WEBHOOK, content=body + b" ", headers=headers).status_code == 400
    big = b"x" * 1_000_001
    assert client.post(WEBHOOK, content=big, headers=headers).status_code == 413

    def chunks():  # no Content-Length: the body itself is measured
        for _ in range(11):
            yield b"x" * 100_000

    assert client.post(WEBHOOK, content=chunks(), headers=headers).status_code == 413
    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", "")
    assert client.post(WEBHOOK, content=body, headers=headers).status_code == 503
    assert db.execute(
        "select count(*) from public.stripe_events where event_id = %s", (payload["id"],)
    ).fetchone() == (0,)


def test_a_card_payment_is_recorded_once(client: TestClient, shop, db, fake) -> None:
    placed = place(client, shop)
    token = placed["public_token"]
    paid = session_completed(db, shop, token)
    deliver(client, paid)
    inv = invoice(db, token)
    assert (inv["status"], inv["method"], inv["paid"]) == ("paid", "card", TOTAL)
    assert stock(db, shop) == 2
    deliver(client, paid)  # a replay
    deliver(client, {**paid, "id": f"evt_it_{uuid.uuid4().hex[:16]}"})  # a second event
    assert (invoice(db, token)["paid"], stock(db, shop)) == (TOTAL, 2)
    tracked = client.get(
        f"/api/v1/public/orders/{token}", headers={"Origin": shop["origin"]}
    ).json()
    assert (tracked["status"], tracked["balance_cents"]) == ("paid", 0)
    # In the admin: a card payment, not refundable in DELEA, not voidable.
    owner = shop["tenant"].headers("owner")
    seen = client.get(f"{INVOICES}/{inv['id']}", headers=owner).json()
    assert [(p["provider"], p["method"], p["status"]) for p in seen["payments"]] == [
        ("stripe", "card", "succeeded")
    ]
    refund = client.post(f"{INVOICES}/{inv['id']}/refund", headers=owner)
    assert refund.status_code == 409 and "Refund it in Stripe" in refund.json()["detail"]


def test_events_that_dont_match_change_nothing(client: TestClient, shop, db, fake) -> None:
    token = place(client, shop)["public_token"]
    for wrong in (
        session_completed(db, shop, token, amount=1),
        session_completed(db, shop, token, account="acct_ITsomeoneelse"),
        session_completed(db, shop, token, live=True),
    ):
        deliver(client, wrong)  # 200: retrying would never help
    assert invoice(db, token)["status"] == "quote"
    outcomes = db.execute(
        "select outcome from public.stripe_events where event_id like 'evt_it_%' order by outcome"
    ).fetchall()
    assert [o for (o,) in outcomes] == ["live_refused", "no_match", "no_match"]


def test_stock_short_at_payment_keeps_the_payment_and_a_replay_changes_nothing(
    client: TestClient, shop, db, fake
) -> None:
    """The extra requirement: settle runs in the same transaction as the payment row and the
    event row; when it can't complete (stock short) the payment is kept, the invoice stays a
    quote with stock_short (the admin's "paid, stock short" banner), and a replay is a no-op."""
    token = place(client, shop)["public_token"]
    db.execute("update public.products set stock_qty = 0 where id = %s", (shop["ring"],))
    paid = session_completed(db, shop, token)
    deliver(client, paid)
    inv = invoice(db, token)
    assert (inv["status"], inv["paid"]) == ("quote", TOTAL)
    owner = shop["tenant"].headers("owner")
    seen = client.get(f"{INVOICES}/{inv['id']}", headers=owner).json()
    assert (seen["stock_short"], seen["balance_cents"]) == (True, 0)
    assert db.execute(
        "select outcome, tenant_id::text from public.stripe_events where event_id = %s",
        (paid["id"],),
    ).fetchone() == ("stock_short", shop["tenant"].id)
    deliver(client, paid)
    assert invoice(db, token) == inv
    assert stock(db, shop) == 0
    # Restocked, the owner completes it.
    db.execute("update public.products set stock_qty = 1 where id = %s", (shop["ring"],))
    done = client.post(f"{INVOICES}/{inv['id']}/settle", headers=owner)
    assert done.status_code == 200 and done.json()["order_status"] == "paid"


def test_a_failure_rolls_the_event_back_and_the_retry_applies_it(
    client: TestClient, shop, db, fake, monkeypatch
) -> None:
    token = place(client, shop)["public_token"]
    paid = session_completed(db, shop, token)
    original = stripe_webhooks._apply

    async def applied_then_crash(conn, ev):
        await original(conn, ev)
        raise RuntimeError("the process died after the effects")

    monkeypatch.setattr(stripe_webhooks, "_apply", applied_then_crash)
    with pytest.raises(RuntimeError):
        deliver(client, paid)
    assert invoice(db, token)["status"] == "quote"
    assert db.execute(
        "select count(*) from public.stripe_events where event_id = %s", (paid["id"],)
    ).fetchone() == (0,)
    monkeypatch.setattr(stripe_webhooks, "_apply", original)
    deliver(client, paid)  # Stripe's retry
    assert invoice(db, token)["status"] == "paid"


def test_refunds_from_the_stripe_dashboard(client: TestClient, shop, db, fake) -> None:
    token = place(client, shop)["public_token"]
    paid = session_completed(db, shop, token)
    deliver(client, paid)
    intent = paid["data"]["object"]["payment_intent"]

    def refund(kind, refund_id, amount, status):
        return event(
            f"refund.{kind}",
            {
                "id": refund_id,
                "object": "refund",
                "amount": amount,
                "currency": "usd",
                "payment_intent": intent,
                "status": status,
            },
            shop["account"],
        )

    deliver(client, refund("created", "re_it_1", 5000, "succeeded"))
    inv = invoice(db, token)
    assert (inv["paid"], inv["refunded_at"]) == (TOTAL - 5000, None)
    late = refund("updated", "re_it_1", 5000, "pending")
    deliver(client, late)  # older news arriving late
    assert invoice(db, token)["paid"] == TOTAL - 5000
    deliver(client, refund("created", "re_it_2", TOTAL - 5000, "succeeded"))
    inv = invoice(db, token)
    assert inv["paid"] == 0 and inv["refunded_at"] is not None
    assert stock(db, shop) == 2  # a refund never restocks


def test_account_events(client: TestClient, shop, db, fake) -> None:
    origin = {"Origin": shop["origin"]}
    deliver(
        client,
        event(
            "account.updated",
            {
                "id": shop["account"],
                "object": "account",
                "charges_enabled": False,
                "details_submitted": True,
            },
            shop["account"],
        ),
    )
    store = client.get("/api/v1/public/store", headers=origin).json()
    assert "card" not in store["payment_choices"]
    deliver(
        client,
        event(
            "account.updated",
            {
                "id": shop["account"],
                "object": "account",
                "charges_enabled": True,
                "details_submitted": True,
            },
            shop["account"],
        ),
    )
    assert "card" in client.get("/api/v1/public/store", headers=origin).json()["payment_choices"]
    deliver(
        client,
        event(
            "account.application.deauthorized",
            {"id": "ca_it", "object": "application"},
            shop["account"],
        ),
    )
    assert (
        "card" not in client.get("/api/v1/public/store", headers=origin).json()["payment_choices"]
    )


# ---------------------------------------------------------------------------
# A payment recorded by hand while a card session is open
# ---------------------------------------------------------------------------
def test_a_manual_payment_expires_the_open_card_session_first(
    client: TestClient, shop, db, fake
) -> None:
    token = place(client, shop)["public_token"]
    inv = invoice(db, token)
    _, session_id, _, _ = pending(db, inv["id"])
    owner = shop["tenant"].headers("owner")

    fake.fail.add("expire_checkout_session")
    refused = client.post(
        f"{INVOICES}/{inv['id']}/payments",
        json={"amount_cents": TOTAL, "method": "bank_transfer"},
        headers=owner,
    )
    assert refused.status_code == 409 and "card payment is open" in refused.json()["detail"]
    assert invoice(db, token)["paid"] == 0

    done = client.post(
        f"{INVOICES}/{inv['id']}/payments",
        json={"amount_cents": TOTAL, "method": "bank_transfer"},
        headers=owner,
    )
    assert done.status_code == 201 and done.json()["order_status"] == "paid"
    assert fake.sessions[session_id]["status"] == "expired"
    assert db.execute(
        "select status from public.payments where external_ref = %s", (session_id,)
    ).fetchone() == ("expired",)
