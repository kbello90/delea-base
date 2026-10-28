"""Customer emails with the database and the worker (Slice D2): order received, deposit
received, in production (once), payment received and shipped (with the carrier and tracking
number), each switchable per shop with an intro of its own; never the street address or phone
number; website orders only; demo shops never email shoppers; the preview and the test of a
customer email go nowhere but the owner.

Shops are throwaway tenants and the worker sends through a recording sender."""

import psycopg
import pytest

from app.core.config import get_settings
from app.worker import Worker

from . import test_email as d1
from .test_email import SETTINGS_URL, Recorder, messages, place

# The D1 email tests' fixtures: a throwaway shop with a website, the pool, and a quiet queue.
shop = d1.shop
pool = d1.pool
_quiet_queue = d1._quiet_queue

pytestmark = pytest.mark.integration

SECRETS = ("Main St", "010-0142", "Portland", "04101")


def invoice_of(db: psycopg.Connection, tenant_id: str, order_ref: str) -> str:
    return str(
        db.execute(
            "select id from public.invoices where tenant_id = %s and order_ref = %s",
            (tenant_id, order_ref),
        ).fetchone()[0]
    )


def to_shopper(recorder: Recorder) -> list:
    return [e for e in recorder.sent if e.recipient == "sarah@example.com"]


@pytest.mark.anyio
async def test_an_order_from_placed_to_shipped(client, shop, db, pool) -> None:
    owner = shop["tenant"].headers("owner")
    recorder = Recorder()
    worker = Worker(pool, recorder, get_settings())
    order = place(client, shop)
    ref, token = order["order_ref"], order["public_token"]
    invoice = invoice_of(db, shop["tenant"].id, ref)
    tracking = f"{shop['origin']}/orders/{token}"
    await worker.run_once()

    (received,) = to_shopper(recorder)
    assert received.subject == f"Order {ref} received"
    assert "Halo Ring × 1" in received.text and "$162.60" in received.text
    assert "Transfer to account 000 (test)." in received.text  # how to pay
    assert f"Track your order: {tracking}" in received.text
    assert f'href="{tracking}"' in received.html
    assert "You're receiving this because you placed an order with" in received.text
    assert "<!doctype html>" in received.html and "<script" not in received.html

    # A deposit, then production, then the rest.
    deposit = client.post(
        f"/api/v1/invoices/{invoice}/payments",
        json={"amount_cents": 5000, "method": "bank_transfer"},
        headers=owner,
    )
    assert deposit.status_code == 201, deposit.text
    await worker.run_once()
    deposit_email = to_shopper(recorder)[-1]
    assert deposit_email.subject == f"Deposit received for {ref}"
    assert "We received your deposit of $50.00" in deposit_email.text
    assert "Left to pay: $112.60" in deposit_email.text

    started = client.post(f"/api/v1/invoices/{invoice}/start-production", headers=owner)
    assert started.status_code == 200, started.text
    rest = client.post(
        f"/api/v1/invoices/{invoice}/payments",
        json={"amount_cents": 11260, "method": "bank_transfer"},
        headers=owner,
    )
    assert rest.status_code == 201 and rest.json()["order_status"] == "in_production"
    await worker.run_once()
    subjects = [e.subject for e in to_shopper(recorder)]
    # Queued in the same pass, so in no particular order.
    assert set(subjects[-2:]) == {f"Order {ref} is being made", f"Payment received for {ref}"}
    # Production started, then the status became in production: one email, not two.
    assert subjects.count(f"Order {ref} is being made") == 1

    # Shipped with a carrier and tracking number.
    shipped = client.post(
        f"/api/v1/invoices/{invoice}/status",
        json={"status": "shipped", "carrier": " USPS ", "tracking_number": "9400 1000-0000"},
        headers=owner,
    )
    assert shipped.status_code == 200, shipped.text
    assert (shipped.json()["carrier"], shipped.json()["tracking_number"]) == (
        "USPS",
        "9400 1000-0000",
    )
    await worker.run_once()
    last = to_shopper(recorder)[-1]
    assert last.subject == f"Order {ref} has shipped"
    assert "Carrier: USPS" in last.text and "Tracking number: 9400 1000-0000" in last.text
    page = client.get(f"/api/v1/public/orders/{token}", headers={"Origin": shop["origin"]})
    assert (page.json()["carrier"], page.json()["tracking_number"]) == ("USPS", "9400 1000-0000")

    # Never the street address or phone number, in any of them.
    for email in to_shopper(recorder):
        for secret in SECRETS:
            assert secret not in email.html + email.text + email.subject, (email.subject, secret)
    assert len(to_shopper(recorder)) == 5
    rows = [m for m in messages(db, shop["tenant"].id) if m[3] == "customer"]
    assert {m[0] for m in rows} == {
        "order_received",
        "deposit_received",
        "in_production",
        "payment_received",
        "shipped",
    }

    # A replay queues nothing new.
    db.execute(
        "update public.automation_events set processed_at = null where tenant_id = %s",
        (shop["tenant"].id,),
    )
    await worker.run_once()
    assert len(to_shopper(recorder)) == 5


@pytest.mark.anyio
async def test_a_late_worker_still_describes_the_deposit_as_it_was(client, shop, db, pool) -> None:
    owner = shop["tenant"].headers("owner")
    order = place(client, shop)
    invoice = invoice_of(db, shop["tenant"].id, order["order_ref"])
    for cents in (5000, 11260):  # a deposit, then the rest, before the worker runs
        paid = client.post(
            f"/api/v1/invoices/{invoice}/payments",
            json={"amount_cents": cents, "method": "bank_transfer"},
            headers=owner,
        )
        assert paid.status_code == 201, paid.text
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()
    by_subject = {e.subject.split(" for ")[0]: e for e in to_shopper(recorder)}
    deposit = by_subject["Deposit received"]
    assert "Paid so far: $50.00" in deposit.text and "Left to pay: $112.60" in deposit.text
    assert "Payment received" in by_subject  # and the payment in full has its own email


@pytest.mark.anyio
async def test_switches_and_intros(client, shop, db, pool) -> None:
    owner = shop["tenant"].headers("owner")
    current = client.get(SETTINGS_URL, headers=owner).json()["customer"]
    assert all(e["enabled"] for e in current.values())
    customer = {
        **current,
        "order_received": {"enabled": True, "intro": "  <b>Thank you</b> & welcome!  "},
        "shipped": {"enabled": False, "intro": None},
    }
    saved = client.patch(SETTINGS_URL, json={"customer": customer}, headers=owner)
    assert saved.status_code == 200, saved.text
    assert saved.json()["customer"]["order_received"]["intro"] == "<b>Thank you</b> & welcome!"
    assert saved.json()["customer"]["shipped"]["enabled"] is False
    too_long = {**customer, "order_received": {"enabled": True, "intro": "x" * 301}}
    assert client.patch(SETTINGS_URL, json={"customer": too_long}, headers=owner).status_code == 422

    recorder = Recorder()
    worker = Worker(pool, recorder, get_settings())
    order = place(client, shop)
    invoice = invoice_of(db, shop["tenant"].id, order["order_ref"])
    await worker.run_once()
    (received,) = to_shopper(recorder)
    assert received.text.splitlines()[4] == "<b>Thank you</b> & welcome!"  # first line, as typed
    assert "&lt;b&gt;Thank you&lt;/b&gt; &amp; welcome!" in received.html  # and escaped
    assert "<b>Thank you</b>" not in received.html

    paid = client.post(
        f"/api/v1/invoices/{invoice}/status",
        json={"status": "paid", "payment_method": "bank_transfer"},
        headers=owner,
    )
    assert paid.status_code == 200, paid.text
    ship = client.post(
        f"/api/v1/invoices/{invoice}/status", json={"status": "shipped"}, headers=owner
    )
    assert ship.status_code == 200, ship.text
    await worker.run_once()
    assert [e.subject.split(" ")[0] for e in to_shopper(recorder)] == ["Order", "Payment"]
    assert "shipped" not in {m[0] for m in messages(db, shop["tenant"].id)}  # switched off


@pytest.mark.anyio
async def test_only_website_orders_and_never_from_a_demo_shop(
    client, shop, make_tenant, db, pool
) -> None:
    owner = shop["tenant"].headers("owner")
    recorder = Recorder()
    worker = Worker(pool, recorder, get_settings())
    customer = client.post(
        "/api/v1/customers",
        json={"first_name": "Counter", "email": "counter@example.com"},
        headers=owner,
    )
    assert customer.status_code == 201, customer.text
    sale = client.post(
        "/api/v1/invoices",
        json={
            "customer_id": customer.json()["id"],
            "items": [{"product_id": shop["ring"], "qty": 1}],
            "pay_now": True,
            "payment_method": "cash",
        },
        headers=owner,
    )
    assert sale.status_code == 201, sale.text
    await worker.run_once()
    assert [m for m in messages(db, shop["tenant"].id) if m[3] == "customer"] == []

    # A demo shop: the shopper's emails are kept as not sent, with the reason.
    db.execute("update public.tenants set is_demo = true where id = %s", (shop["tenant"].id,))
    try:
        place(client, shop)
        await worker.run_once()
    finally:
        db.execute("update public.tenants set is_demo = false where id = %s", (shop["tenant"].id,))
    assert to_shopper(recorder) == []
    rows = db.execute(
        "select status, last_error from public.email_messages "
        "where tenant_id = %s and kind = 'customer'",
        (shop["tenant"].id,),
    ).fetchall()
    assert rows == [("suppressed", "demo shop: only its own owners get email")]


def test_a_carrier_only_goes_with_marking_it_shipped(client, shop, db) -> None:
    owner = shop["tenant"].headers("owner")
    order = place(client, shop)
    invoice = invoice_of(db, shop["tenant"].id, order["order_ref"])
    url = f"/api/v1/invoices/{invoice}/status"
    early = client.post(
        url,
        json={"status": "paid", "payment_method": "cash", "tracking_number": "9400 1"},
        headers=owner,
    )
    assert early.status_code == 422
    paid = client.post(url, json={"status": "paid", "payment_method": "cash"}, headers=owner)
    assert paid.status_code == 200, paid.text
    for bad in ({"tracking_number": "<b>1</b>"}, {"tracking_number": "x" * 41}, {"carrier": "<i>"}):
        refused = client.post(url, json={"status": "shipped", **bad}, headers=owner)
        assert refused.status_code == 422, bad
    blank = client.post(
        url, json={"status": "shipped", "carrier": "", "tracking_number": " "}, headers=owner
    )
    assert blank.status_code == 200 and blank.json()["tracking_number"] is None
    again = client.post(
        url, json={"status": "shipped", "tracking_number": "9400 1000"}, headers=owner
    )
    assert again.status_code == 422  # already shipped: nothing to attach it to


def test_the_preview_queues_nothing_and_is_owner_only(client, shop, db) -> None:
    owner = shop["tenant"].headers("owner")
    for template in (
        "order_received",
        "payment_received",
        "deposit_received",
        "in_production",
        "shipped",
    ):
        out = client.post(
            f"{SETTINGS_URL}/preview",
            json={"template": template, "intro": "<script>alert(1)</script>"},
            headers=owner,
        )
        assert out.status_code == 200, out.text
        body = out.json()
        assert "W-SAMPLE01" in body["subject"] + body["text"]
        assert "<script>" not in body["html"] and "&lt;script&gt;" in body["html"]
        assert body["text"].endswith("\n")
        assert shop["origin"] in body["text"]  # the shop's own website, from its domain
    staff = client.post(
        f"{SETTINGS_URL}/preview",
        json={"template": "shipped"},
        headers=shop["tenant"].headers("staff"),
    )
    assert staff.status_code == 403
    assert (
        client.post(
            f"{SETTINGS_URL}/preview", json={"template": "marketing"}, headers=owner
        ).status_code
        == 422
    )
    assert messages(db, shop["tenant"].id) == []


@pytest.mark.anyio
async def test_a_customer_email_test_goes_to_the_owner_only(client, shop, db, pool) -> None:
    owner = shop["tenant"].headers("owner")
    out = client.post(f"{SETTINGS_URL}/test", json={"template": "shipped"}, headers=owner)
    assert out.status_code == 200, out.text
    assert out.json()["sent_to"] == shop["owner"]
    recorder = Recorder()
    await Worker(pool, recorder, get_settings()).run_once()
    (sent,) = recorder.sent
    assert sent.recipient == shop["owner"]
    assert sent.subject == "[Test] Order W-SAMPLE01 has shipped"
    assert [m[:4] for m in messages(db, shop["tenant"].id)] == [
        ("shipped", shop["owner"], "sent", "test")
    ]
    # The plain test still works without a body.
    assert client.post(f"{SETTINGS_URL}/test", headers=owner).status_code == 200
    refused = client.post(f"{SETTINGS_URL}/test", json={"to": "x@example.com"}, headers=owner)
    assert refused.status_code == 422
